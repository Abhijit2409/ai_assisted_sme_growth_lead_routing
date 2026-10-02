"""
Synthetic CRM data generator (hypothetical process, fictional bank, fictional businesses and staff).

Simulates one synthetic operating year (2025-06-01 to 2026-05-31 UTC) of the hypothetical SME acquisition process
specified by the design catalogs in data/design/crm. Every setting is read from those catalogs: lifecycle
transitions, SLA rules and business calendar, capacity weights and thresholds, specialist rules, duplicate rules
and the declared generation parameters (synthetic_generation_parameters.csv). Nothing here is a benchmark or a
statement about any real bank.

Mechanics: a deterministic discrete-event simulation. Each lead is a Python generator that sleeps until its next
event; RM availability changes and nightly capacity snapshots are engine events. Routing decisions replay the
design's documented decision table (eligibility, preference, exclusion, least-loaded with a deterministic
tie-break, fallback queues, overrides) to simulate hypothetical past behaviour. The routing logic is private to this
generator: no ranking, score or recommendation is written out.

Outputs (one Parquet file per logical entity, columns exactly as in crm_field_dictionary.csv):
    data/synthetic/crm/crm_*.parquet, synthetic_crm_manifest.csv, reference/synthetic_fsa_lookup.csv
    data/synthetic/crm_ground_truth/*.parquet and ground_truth_manifest.csv   (validation only; never a model input)
    data/synthetic/crm/synthetic_crm_run.json                                   (run timestamp; not hashed)

The run writes to a temporary folder, validates it with src/validation/17_validate_synthetic_crm_data.py
(--source-only), and only then replaces the published folders. Exit code 1 on any failure.

    C:/Users/abhij/anaconda3/python.exe src/synthetic/16_generate_synthetic_crm_data.py [--output-root DIR] [--no-validate] [--no-publish]
"""

from pathlib import Path
from bisect import bisect_left, bisect_right
from decimal import Decimal, ROUND_HALF_UP
import argparse
import csv
import datetime as dt
import hashlib
import heapq
import json
import math
import re
import shutil
import subprocess
import sys
import zoneinfo

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "data" / "design" / "crm"
PUBLISH = ROOT / "data" / "synthetic" / "crm"
PUBLISH_TRUTH = ROOT / "data" / "synthetic" / "crm_ground_truth"
WAREHOUSE = ROOT / "data" / "warehouse" / "sme_banking.duckdb"
VALIDATOR = ROOT / "src" / "validation" / "17_validate_synthetic_crm_data.py"
UTC = dt.timezone.utc
DAY = 86400
PROVINCES = ["10", "11", "12", "13", "24", "35", "46", "47", "48", "59", "60", "61", "62"]
REGION = {"10": "Atlantic", "11": "Atlantic", "12": "Atlantic", "13": "Atlantic", "24": "Quebec", "35": "Ontario", "46": "Prairies",
          "47": "Prairies", "48": "Prairies", "59": "British Columbia and North", "60": "British Columbia and North",
          "61": "British Columbia and North", "62": "British Columbia and North"}
FSA_LETTERS = {"10": "A", "11": "C", "12": "B", "13": "E", "24": "GHJ", "35": "KLMNP", "46": "R", "47": "S", "48": "T", "59": "V", "60": "Y",
               "61": "X", "62": "X"}
AREA_CODE = {"10": "709", "11": "902", "12": "902", "13": "506", "24": "514", "35": "416", "46": "204", "47": "306", "48": "403", "59": "604",
             "60": "867", "61": "867", "62": "867", None: "613"}
MICRO = {"1-4", "5-19"}
ACTIVE_LEAD = {"READY_FOR_ASSIGNMENT", "ASSIGNED", "CONTACTED", "NURTURING", "QUALIFIED"}


class Interrupt(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# ============================================================ configuration (all from the design catalogs)
def read_csv(name):
    with open(DESIGN / name, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def iso_to_epoch(s):
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp())


class Config:
    def __init__(self):
        self.p = {r["parameter_name"]: r["value"] for r in read_csv("synthetic_generation_parameters.csv")}
        self.fields = read_csv("crm_field_dictionary.csv")
        self.entities = {r["table_name"]: r for r in read_csv("crm_entity_catalog.csv")}
        self.trans = {(r["from_status"], r["to_status"]): r for r in read_csv("lead_transition_rules.csv")}
        self.terminal = {r["status_code"] for r in read_csv("lead_status_catalog.csv") if r["is_terminal"] == "true"}
        self.sla = {}
        for r in read_csv("sla_rule_catalog.csv"):
            unit = 3600 if r["target_unit"] == "hours" else (8.5 * 3600 if r["time_basis"] == "business" else DAY)
            self.sla[r["sla_id"]] = {"secs": int(float(r["target_value"]) * unit), "basis": r["time_basis"], "warning_pct": int(r["warning_pct"]),
                                     "role": r["responsible_role"], "esc1": r["escalation_level_1"], "esc2": r["escalation_level_2"],
                                     "start": r["start_event"], "stops": set(r["stop_events"].split(";")), "applies": r["applies_to"]}
        cap = {r["rule_id"]: r for r in read_csv("rm_capacity_rule_catalog.csv")}
        self.w_new, self.w_contacted, self.w_qualified, self.w_support = (float(cap[k]["weight"]) for k in ("CAP-01", "CAP-02", "CAP-03", "CAP-04"))
        m = re.search(r"more than (\d+) days away weigh ([0-9.]+)", cap["CAP-02"]["exception_handling"])
        self.nurture_far_days, self.w_nurture_far = int(m.group(1)), float(m.group(2))
        self.base_capacity = float(cap["CAP-05"]["threshold"])
        self.warning, self.hard = float(cap["CAP-06"]["warning_level"]), float(cap["CAP-06"]["hard_limit"])
        self.exception_ceiling = float(cap["CAP-09"]["hard_limit"])
        self.exception_days = int(re.search(r"Maximum (\d+) business days", cap["CAP-09"]["exception_handling"]).group(1))
        self.spc = {r["industry_code"]: r for r in read_csv("specialist_rule_catalog.csv")}
        self.dispositions = {r["disposition_code"]: r["applies_to_status"] for r in read_csv("lead_disposition_catalog.csv")}
        self.calendar = read_csv("business_calendar.csv")
        # channel -> SLA ids from the SLA catalog applies_to text
        self.assign_sla, self.fc_sla = {}, {}
        for sid, s in self.sla.items():
            for code in re.findall(r"CH-[A-Z]{3}", s["applies"]):
                if sid.startswith("SLA-04"):
                    self.assign_sla[code] = sid
                if sid.startswith("SLA-06"):
                    self.fc_sla[code] = sid
        self.priority_sla = next(sid for sid, s in self.sla.items() if sid.startswith("SLA-06") and "P2" in s["applies"])
        self.manual_review_sla = next(sid for sid, s in self.sla.items() if sid.startswith("SLA-04") and "manual-review" in s["applies"])
        self.start, self.cutoff = iso_to_epoch(self.p["period_start_utc"]), iso_to_epoch(self.p["period_end_utc"])
        self.seed = int(self.p["random_seed"])

    def f(self, k):
        return float(self.p[k])

    def i(self, k):
        return int(float(self.p[k]))

    def rng_range(self, rng, k):
        lo, hi = (float(x) for x in self.p[k].split("-"))
        return rng.uniform(lo, hi)


# ============================================================ business calendar (epoch seconds, precomputed per province)
class Calendar:
    def __init__(self, cfg):
        s = {r["description"]: r["value"] for r in cfg.calendar if r["entry_type"] == "setting"}
        self.day_start = tuple(int(x) for x in s["business_day_start_local"].split(":"))
        self.day_end = tuple(int(x) for x in s["business_day_end_local"].split(":"))
        self.zone = {p: s[f"zone_{p}"] for p in PROVINCES}
        self.zone[None] = s["zone_default"]
        self.hol = {p: set() for p in PROVINCES + [None, "ALL"]}
        for r in cfg.calendar:
            if r["entry_type"] == "holiday":
                d = dt.date.fromisoformat(r["calendar_date"])
                for p in r["province_codes"].split(";"):
                    self.hol[p].add(d)
        self.first, self.last = dt.date(2025, 5, 15), dt.date(2026, 9, 30)
        self.tab = {p: self._build(p) for p in PROVINCES + [None]}
        # national business days (capacity snapshots): weekdays excluding holidays that apply to ALL provinces
        self.national_days = [d for d in self._dates() if d.weekday() < 5 and d not in self.hol["ALL"]]

    def _dates(self):
        d = self.first
        while d <= self.last:
            yield d
            d += dt.timedelta(days=1)

    def _build(self, p):
        z = zoneinfo.ZoneInfo(self.zone[p])
        mid, ws, we, cum = [], [], [], []
        c = 0
        for d in self._dates():
            mid.append(int(dt.datetime(d.year, d.month, d.day, tzinfo=z).timestamp()))
            biz = d.weekday() < 5 and d not in self.hol["ALL"] and (p is None or d not in self.hol[p])
            a = int(dt.datetime(d.year, d.month, d.day, *self.day_start, tzinfo=z).timestamp())
            b = int(dt.datetime(d.year, d.month, d.day, *self.day_end, tzinfo=z).timestamp()) if biz else a
            ws.append(a)
            we.append(b)
            cum.append(c)
            c += b - a
        after = [cum[i] + we[i] - ws[i] for i in range(len(cum))]
        return mid, ws, we, cum, after

    def f(self, p, t):
        mid, ws, we, cum, _ = self.tab[p]
        i = bisect_right(mid, t) - 1
        return cum[i] + min(max(t - ws[i], 0), we[i] - ws[i])

    def add(self, p, t, secs):
        if secs <= 0:
            return t
        mid, ws, we, cum, after = self.tab[p]
        target = self.f(p, t) + secs
        i = bisect_left(after, target)
        return ws[i] + (target - cum[i])

    def between(self, p, a, b):
        return self.f(p, b) - self.f(p, a)

    def local_hm(self, p, t):
        return dt.datetime.fromtimestamp(t, zoneinfo.ZoneInfo(self.zone[p]))

    def at_local(self, p, d, hour, minute):
        return int(dt.datetime(d.year, d.month, d.day, hour, minute, tzinfo=zoneinfo.ZoneInfo(self.zone[p])).timestamp())

    def is_business_date(self, p, d):
        return d.weekday() < 5 and d not in self.hol["ALL"] and (p is None or d not in self.hol[p])


# ============================================================ reference data (read-only warehouse)
def load_reference():
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    cma = con.execute("select cma_code_sgc2021, province_code, tier from analytics.dim_cma order by cma_code_sgc2021").fetchall()
    scale = dict(con.execute("select cma_code_sgc2021, locations from analytics.fact_cma_market_scale_snapshot "
                             "where metric_id = 'S02' order by 1").fetchall())
    comp = con.execute("select cma_code_sgc2021, naics_sector, sme_locations from analytics.fact_cma_sector_composition_snapshot "
                       "where sme_locations is not null and naics_sector not in ('UNCLASSIFIED', '91') order by 1, 2").fetchall()
    inds = {r[0] for r in con.execute("select industry_code from analytics.dim_industry where industry_system = 'NAICS2022_1097'").fetchall()}
    bands = {r[0] for r in con.execute("select size_band_key from analytics.dim_size_band").fetchall()}
    con.close()
    sectors = {}
    for c, s, n in comp:
        sectors.setdefault(c, {})[s] = sectors.setdefault(c, {}).get(s, 0) + float(n)
    return cma, scale, sectors, inds, bands


def choice(rng, items, weights=None):
    if weights is None:
        return items[int(rng.integers(len(items)))]
    w = np.asarray(weights, dtype=float)
    return items[int(rng.choice(len(items), p=w / w.sum()))]


def fmt_id(prefix, n, width):
    return f"{prefix}-{n:0{width}d}"


# ============================================================ simulation engine
class Engine:
    def __init__(self, cfg, cal, ref):
        self.cfg, self.cal = cfg, cal
        self.cma_rows, self.scale, self.sectors, self.industries, self.bands = ref
        self.heap, self.seq, self.now = [], 0, cfg.start
        self.T = {k: [] for k in ["prospect", "lead", "touch", "channel", "campaign", "rm", "territory", "rm_territory", "rm_spec",
                                  "snapshot", "decision", "assignment", "activity", "status", "sla", "dup", "opp", "rule_version"]}
        self.truth, self.membership, self.prospect_truth, self.decision_truth = {}, [], [], []
        self.counters = {}
        self.leads, self.prospects = {}, {}
        self.rng = np.random.default_rng([cfg.seed, 1])

    # ---------- ids and time helpers
    def nid(self, prefix, width):
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return fmt_id(prefix, self.counters[prefix], width)

    def push(self, t, prio, kind, data):
        self.seq += 1
        heapq.heappush(self.heap, (t, prio, self.seq, kind, data))

    def bsecs(self, days):
        return int(days * 8.5 * 3600)

    def badd(self, L, t, secs):
        return self.cal.add(L["prov_key"], t, int(secs))

    # ---------- reference build
    def build_reference(self):
        cfg = self.cfg
        rng = np.random.default_rng([cfg.seed, 2])
        start = cfg.start
        # rule versions
        files = {"routing": ["routing_rule_catalog.csv"], "sla": ["sla_rule_catalog.csv", "business_calendar.csv"],
                 "capacity": ["rm_capacity_rule_catalog.csv"], "duplicate": ["duplicate_rule_catalog.csv"],
                 "territory": ["territory_rule_catalog.csv"], "specialist": ["specialist_rule_catalog.csv"]}
        for rs, fl in files.items():
            h = hashlib.sha256(b"".join((DESIGN / f).read_bytes() for f in fl)).hexdigest()
            self.T["rule_version"].append({"rule_version_id": f"RV-{rs.upper()}-001", "rule_set": rs, "version_number": 1,
                                           "effective_from": start, "effective_to": None, "config_sha256": h,
                                           "approved_by_role": "Director, Business Banking Sales Strategy", "created_at": start,
                                           "updated_at": None, "record_source": "synthetic_generator"})
        # channels
        names = {"CH-DIG": "Digital lead", "CH-BRN": "Branch referral", "CH-MKT": "Marketing campaign", "CH-PTR": "Partner referral",
                 "CH-RMP": "RM prospecting"}
        for c, n in names.items():
            self.T["channel"].append({"channel_code": c, "channel_name": n, "default_assignment_sla_id": cfg.assign_sla[c],
                                      "created_at": start, "updated_at": None, "record_source": "synthetic_generator"})
        # territories
        self.cma = {c: {"prov": p, "tier": int(t), "provs": p.split(";")} for c, p, t in self.cma_rows}
        tot = sum(self.scale.values())
        floor = cfg.f("cma_floor_share")
        raw = {c: max(self.scale.get(c, 0) / tot, floor) for c in self.cma}
        s = sum(raw.values())
        self.cma_w = {c: v / s for c, v in raw.items()}
        self.terr = {}
        for c in sorted(self.cma):
            prov0 = self.cma[c]["provs"][-1] if c == "505" else self.cma[c]["prov"]
            self.terr[f"TER-CMA-{c}"] = {"type": "cma", "cma": c, "prov": None, "region": REGION[prov0 if c != "505" else "35"],
                                         "tier": self.cma[c]["tier"]}
        self.remainder_provs = [p.split(":")[0] for p in cfg.p["province_remainder_weights"].split(";")]
        self.remainder_w = [float(p.split(":")[1]) for p in cfg.p["province_remainder_weights"].split(";")]
        for p in self.remainder_provs:
            self.terr[f"TER-PRV-{p}"] = {"type": "province_remainder", "cma": None, "prov": p, "region": REGION[p], "tier": None}
        self.terr["TER-REMOTE-NORTH"] = {"type": "remote_service", "cma": None, "prov": None, "region": "British Columbia and North", "tier": None}
        self.terr["TER-NATIONAL-SPECIALIST"] = {"type": "national_specialist", "cma": None, "prov": None, "region": "National", "tier": None}
        for tid, t in self.terr.items():
            self.T["territory"].append({"territory_id": tid, "territory_code": tid[4:], "territory_type": t["type"], "cma_code_sgc2021": t["cma"],
                                        "province_code": t["prov"], "sales_region": t["region"], "evidence_tier": t["tier"],
                                        "created_at": start, "updated_at": None, "record_source": "synthetic_generator"})
        # synthetic FSA lookup
        used, self.fsa_of_cma, self.fsa_of_prov, self.fsa_rows = set(), {}, {}, []
        letters2 = "ABCEGHJKLMNPRSTVWXYZ"

        def new_fsa(prov):
            while True:
                f = choice(rng, list(FSA_LETTERS[prov])) + str(int(rng.integers(1, 10))) + choice(rng, list(letters2))
                if f not in used:
                    used.add(f)
                    return f
        for c in sorted(self.cma):
            k = 3 + int(round(9 * math.sqrt(self.cma_w[c] / max(self.cma_w.values()))))
            for prov in self.cma[c]["provs"]:
                fs = [new_fsa(prov) for _ in range(k if len(self.cma[c]["provs"]) == 1 else max(2, k // 2))]
                self.fsa_of_cma.setdefault(c, []).extend((f, prov) for f in fs)
                self.fsa_rows += [{"postal_fsa": f, "province_code": prov, "cma_code_sgc2021": c, "resolution": "resolved_cma"} for f in fs]
        for p in PROVINCES:
            fs = [new_fsa(p) for _ in range(5 if p not in ("60", "61", "62") else 2)]
            self.fsa_of_prov[p] = fs
            self.fsa_rows += [{"postal_fsa": f, "province_code": p, "cma_code_sgc2021": None,
                               "resolution": "resolved_province_remainder"} for f in fs]
        self.fsa_rows.sort(key=lambda r: r["postal_fsa"])
        # campaigns
        n = cfg.i("campaign_count")
        span = (cfg.cutoff - start) // DAY - 50
        for k in range(n):
            s0 = start + int(k * span / n) * DAY
            dur = int(rng.integers(21, 57))
            s_date = dt.datetime.fromtimestamp(s0, UTC).date()
            e_date = s_date + dt.timedelta(days=dur)
            q = (s_date.month - 1) // 3 + 1
            self.T["campaign"].append({"campaign_id": fmt_id("CMP", k + 1, 4), "campaign_code": f"CMP-FICT-{s_date.year}-Q{q}-{k + 1:02d}",
                                       "campaign_name": f"Campaign Synthetic {k + 1:03d} (fictional)",
                                       "target_segment": choice(rng, ["micro", "established", "all"], [0.4, 0.25, 0.35]),
                                       "campaign_start_date": s_date, "campaign_end_date": e_date, "created_at": s0, "updated_at": None,
                                       "record_source": "synthetic_generator"})
        self.build_rms(rng)

    def build_rms(self, rng):
        cfg, start = self.cfg, self.cfg.start
        n_total = cfg.i("relationship_manager_count")
        n_reg, n_nat = cfg.i("mandatory_specialists_per_industry_regional"), cfg.i("mandatory_specialists_per_industry_national")
        n_remote = cfg.i("remote_service_rm_count")
        n_gen = n_total - 2 * (n_reg + n_nat) - n_remote - len(self.remainder_provs)
        el = cfg.f("cma_staffing_elasticity")
        w = {c: self.cma_w[c] ** el for c in self.cma}
        sw = sum(w.values())
        quota = {c: max(1.0, n_gen * w[c] / sw) for c in self.cma}
        alloc = {c: int(q) for c, q in quota.items()}
        rest = n_gen - sum(alloc.values())
        for c in sorted(self.cma, key=lambda c: (-(quota[c] - int(quota[c])), c))[:max(rest, 0)]:
            alloc[c] += 1
        while sum(alloc.values()) > n_gen:
            c = max(sorted(alloc), key=lambda c: alloc[c])
            alloc[c] -= 1
        specs = []   # (home territory, segment, dedicated industry, national)
        for c in sorted(self.cma, key=lambda c: -self.cma_w[c]):
            for j in range(alloc[c]):
                seg = "small_business" if (alloc[c] == 1 or j % 2 == 0) else "commercial"
                specs.append((f"TER-CMA-{c}", seg, None, False))
        for p in self.remainder_provs:
            specs.append((f"TER-PRV-{p}", "small_business", None, False))
        for _ in range(n_remote):
            specs.append(("TER-REMOTE-NORTH", "small_business", None, False))
        region_cmas = {}
        for c in self.cma:
            region_cmas.setdefault(self.terr[f"TER-CMA-{c}"]["region"], []).append(c)
        plan = {"11": ["Prairies", "Prairies", "Prairies", "Ontario", "Ontario", "Quebec", "British Columbia and North", "Atlantic"],
                "53": ["Ontario", "Ontario", "Ontario", "Quebec", "Quebec", "British Columbia and North", "British Columbia and North", "Prairies"]}
        for ind, regions in plan.items():
            for reg in regions[:n_reg]:
                cs = sorted(region_cmas[reg], key=lambda c: -(self.sectors.get(c, {}).get(ind, 0)))
                used = sum(1 for s in specs if s[2] == ind and self.terr[s[0]]["region"] == reg)
                specs.append((f"TER-CMA-{cs[used % len(cs)]}", "commercial", ind, False))
            for _ in range(n_nat):
                specs.append(("TER-NATIONAL-SPECIALIST", "commercial", ind, True))
        assert len(specs) == n_total, len(specs)
        self.rms = {}
        leavers = set(rng.choice([i for i, s in enumerate(specs) if s[2] is None and alloc.get(s[0][8:], 0) >= 3],
                                 cfg.i("rm_leavers"), replace=False).tolist())
        joiners = set(rng.choice([i for i, s in enumerate(specs) if s[2] is None and alloc.get(s[0][8:], 0) >= 3 and i not in leavers],
                                 cfg.i("rm_joiners"), replace=False).tolist())
        for i, (home, seg, ind, national) in enumerate(specs):
            rid = fmt_id("RM", i + 1, 4)
            t = self.terr[home]
            prov = t["prov"] or (self.cma[t["cma"]]["prov"] if t["cma"] else None)
            if home == "TER-CMA-505" or prov == "13":
                lang = choice(rng, ["en|fr", "en"], [0.6, 0.4])
            elif prov == "24":
                lang = choice(rng, ["fr", "en|fr"], [0.3, 0.7])
            else:
                lang = choice(rng, ["en", "en|fr"], [0.88, 0.12])
            emp_start = dt.date(2025, 6, 1) - dt.timedelta(days=int(rng.integers(200, 4000)))
            emp_end = None
            if i in joiners:
                emp_start = dt.date(2025, 8, 1) + dt.timedelta(days=int(rng.integers(0, 200)))
            if i in leavers:
                emp_end = dt.date(2025, 9, 1) + dt.timedelta(days=int(rng.integers(0, 220)))
            sec = None
            if ind is None and home.startswith("TER-CMA") and rng.random() < cfg.f("secondary_specialization_share"):
                sec = choice(rng, ["62", "31-33", "23", "54"])
            self.rms[rid] = {"rm_id": rid, "home": home, "segment": seg, "lang": lang, "dedicated": ind, "national": national, "secondary": sec,
                             "emp_start": emp_start, "emp_end": emp_end, "cover": [], "last_assigned": 0,
                             "active": set(), "support": set(), "exception_until": 0}
        # coverage
        cmas_by_region = {}
        for tid, t in self.terr.items():
            if t["type"] == "cma":
                cmas_by_region.setdefault(t["region"], []).append(tid)
        for reg in cmas_by_region:
            cmas_by_region[reg].sort(key=lambda tid: -self.cma_w[self.terr[tid]["cma"]])
        gens = {tid: [r for r in self.rms.values() if r["home"] == tid and r["dedicated"] is None] for tid in self.terr}
        for r in self.rms.values():
            if r["dedicated"] and not r["national"]:
                reg = self.terr[r["home"]]["region"]
                for tid, t in self.terr.items():
                    if t["region"] == reg and t["type"] in ("cma", "province_remainder") or (reg == "British Columbia and North" and t["type"] == "remote_service"):
                        r["cover"].append((tid, "primary"))
            else:
                r["cover"].append((r["home"], "primary"))
        single_no_backup = []
        for reg, tids in sorted(cmas_by_region.items()):
            big = tids[0]
            for tid in tids:
                src = tids[1] if tid == big and len(tids) > 1 else big
                if src == tid or not gens[src]:
                    continue
                if len(gens[tid]) == 1 and rng.random() > cfg.f("backup_coverage_share"):
                    single_no_backup.append(tid)
                    continue
                for r in gens[src][:2]:
                    r["cover"].append((tid, "backup"))
            for p in self.remainder_provs:
                if REGION[p] == reg:
                    for r in gens[big][:2]:
                        r["cover"].append((f"TER-PRV-{p}", "backup"))
            if reg == "British Columbia and North":
                for r in gens[big][:1]:
                    r["cover"].append(("TER-REMOTE-NORTH", "backup"))
        self.uncovered = sorted(single_no_backup)[:cfg.i("uncovered_territory_windows")]
        self.cover_index = {}
        for r in self.rms.values():
            for tid, role in r["cover"]:
                self.cover_index.setdefault(tid, []).append((r["rm_id"], role))
        for tid in self.cover_index:
            self.cover_index[tid].sort()
        # availability calendar (national business days)
        self.avail = {}
        days = [d for d in self.cal.national_days if start <= self.day_epoch(d) < cfg.cutoff]
        self.snapshot_days = days
        forced = {gens[tid][0]["rm_id"]: k for k, tid in enumerate(self.uncovered) if gens[tid]}
        for rid in sorted(self.rms):
            r = self.rms[rid]
            status = {}
            for _ in range(int(rng.poisson(cfg.f("planned_leave_episodes_per_rm")))):
                k0, ln = int(rng.integers(0, len(days))), int(rng.integers(5, 16))
                for d in days[k0:k0 + ln]:
                    status[d] = "planned_leave"
            for _ in range(int(rng.poisson(cfg.f("unplanned_absence_episodes_per_rm")))):
                k0, ln = int(rng.integers(0, len(days))), int(rng.integers(2, 9))
                for d in days[k0:k0 + ln]:
                    status.setdefault(d, "unavailable")
            if rid in forced:
                k0 = 60 + 90 * forced[rid]
                for d in days[k0:k0 + 10]:
                    status[d] = "planned_leave"
            for d in days:
                if d not in status and rng.random() < cfg.f("partial_availability_share"):
                    status[d] = "partial"
            self.avail[rid] = status
        # tables
        for rid in sorted(self.rms):
            r = self.rms[rid]
            vf = max(r["emp_start"], dt.date(2025, 6, 1))
            self.T["rm"].append({"rm_id": rid, "rm_code": f"RM-FICT-{rid[3:]}", "rm_segment": r["segment"], "home_territory_id": r["home"],
                                 "supported_service_languages": r["lang"], "base_capacity_points": self.cfg.base_capacity,
                                 "employment_start_date": r["emp_start"], "employment_end_date": r["emp_end"],
                                 "active_flag": r["emp_end"] is None, "created_at": self.day_epoch(vf),
                                 "updated_at": self.day_epoch(r["emp_end"]) if r["emp_end"] else None, "record_source": "synthetic_generator"})
            for tid, role in r["cover"]:
                self.T["rm_territory"].append({"rm_territory_id": None, "rm_id": rid, "territory_id": tid, "coverage_role": role, "valid_from": vf,
                                               "valid_to": r["emp_end"], "created_at": self.day_epoch(vf), "updated_at": None,
                                               "record_source": "synthetic_generator"})
            for ind, role in ([(r["dedicated"], "primary")] if r["dedicated"] else []) + ([(r["secondary"], "secondary")] if r["secondary"] else []):
                self.T["rm_spec"].append({"rm_specialization_id": None, "rm_id": rid, "industry_system": "NAICS2022_1097", "industry_code": ind,
                                          "specialist_type": self.cfg.spc[ind]["specialist_type"], "specialization_role": role, "valid_from": vf,
                                          "valid_to": r["emp_end"], "created_at": self.day_epoch(vf), "updated_at": None,
                                          "record_source": "synthetic_generator"})
        for k, row in enumerate(self.T["rm_territory"], 1):
            row["rm_territory_id"] = fmt_id("RMT", k, 6)
        for k, row in enumerate(self.T["rm_spec"], 1):
            row["rm_specialization_id"] = fmt_id("RMS", k, 6)
        # engine events: availability changes (priority 0), snapshots (priority 1)
        for rid in sorted(self.rms):
            r = self.rms[rid]
            prev = None
            for d in days:
                st = self.avail[rid].get(d, "available")
                if st in ("planned_leave", "unavailable") and prev not in ("planned_leave", "unavailable"):
                    self.push(self.day_epoch(d), 0, "rm_away", (rid, st))
                prev = st
            if r["emp_end"] and self.day_epoch(r["emp_end"]) < cfg.cutoff:
                self.push(self.day_epoch(r["emp_end"]), 0, "rm_away", (rid, "leaver"))
        for d in days:
            self.push(self.day_epoch(d), 1, "snapshot", d)

    def day_epoch(self, d):
        return int(dt.datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())

    def employed(self, r, t):
        d = dt.datetime.fromtimestamp(t, UTC).date()
        return r["emp_start"] <= d and (r["emp_end"] is None or d < r["emp_end"])

    # ---------- capacity
    def lead_weight(self, lid, t):
        L = self.leads[lid]
        st = L["status"]
        if st == "ASSIGNED":
            return self.cfg.w_new
        if st == "CONTACTED":
            return self.cfg.w_contacted
        if st == "NURTURING":
            if L["nurture_due"] is not None and L["nurture_due"] > t + self.cfg.nurture_far_days * DAY:
                return self.cfg.w_nurture_far
            return self.cfg.w_contacted
        if st == "QUALIFIED":
            return self.cfg.w_qualified
        return 0.0

    def on_snapshot(self, d):
        t = self.day_epoch(d)
        self.snap = getattr(self, "snap", {})
        for rid in sorted(self.rms):
            r = self.rms[rid]
            if not self.employed(r, t):
                self.snap.pop(rid, None)
                continue
            av = self.avail[rid].get(d, "available")
            cap = {"available": 1.0, "partial": 0.5}.get(av, 0.0) * self.cfg.base_capacity
            sup = self.cfg.w_support * len(r["support"])
            wl = sum(self.lead_weight(l, t) for l in sorted(r["active"])) + sup
            ratio = float((Decimal(str(wl)) / Decimal(str(cap))).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)) if cap > 0 else None
            if ratio is None:
                state = "unavailable"
            elif ratio >= self.cfg.hard:
                state = "approved_exception" if (r["exception_until"] > t and ratio <= self.cfg.exception_ceiling) else "at_or_above_hard_limit"
            elif ratio >= self.cfg.warning:
                state = "warning"
            else:
                state = "below_warning"
            self.snap[rid] = {"ratio": ratio, "avail": av, "state": state, "t": t}
            self.T["snapshot"].append({"capacity_snapshot_id": self.nid("CAPS", 7), "rm_id": rid, "snapshot_date": d, "snapshot_as_of_utc": t,
                                       "availability_status": av, "available_capacity_points": cap, "weighted_workload_points": wl,
                                       "utilization_ratio": ratio, "capacity_state": state, "specialist_support_points": sup,
                                       "rule_version_id": "RV-CAPACITY-001", "created_at": t, "updated_at": None,
                                       "record_source": "synthetic_generator"})

    # ---------- lead-level records
    def status(self, L, to, t, disposition=None, reason=None, actor=None, approver=None):
        frm = L["status"]
        if frm is None:
            tid, ev, act = None, "LEAD_CAPTURED", "System (intake)"
        else:
            tr = self.cfg.trans[(frm, to)]
            tid, ev, act = tr["transition_id"], tr["trigger_event"], tr["actor_role"].split(";")[0].strip()
        self.T["status"].append({"status_history_id": self.nid("LSH", 7), "lead_id": L["lead_id"], "from_status": frm, "to_status": to,
                                 "transition_id": tid, "event_code": ev, "changed_at": t, "disposition_code": disposition,
                                 "actor_role": actor or act, "reopen_approver_role": approver, "status_reason": reason,
                                 "created_at": t, "updated_at": None, "record_source": "synthetic_generator"})
        L["status"], L["updated_at"] = to, t
        L["disposition"] = disposition if to in self.cfg.terminal else None
        L["history"].append(to)
        if to in self.cfg.terminal:
            L["closed_at"] = t
            self.fire(L, "LEAD_TERMINATED", t)
            for a in list(L["open_asg"]):
                self.end_assignment(a, t, "COMPLETED")
            if L["opp"] is not None and L["opp"]["opportunity_status"] == "open":
                o = L["opp"]
                o["opportunity_status"] = {"CONVERTED": "won", "CLOSED_LOST": "lost", "WITHDRAWN": "withdrawn"}[to]
                o["closed_at"] = o["updated_at"] = t
                o["outcome_reason"] = disposition
            if to in ("CLOSED_LOST", "WITHDRAWN"):
                self.closed_pool.append(L["lead_id"])

    def touch(self, L, t, channel, extra):
        row = {"source_touch_id": self.nid("TCH", 7), "lead_id": L["lead_id"], "original_lead_id": L["lead_id"], "channel_code": channel,
               "touched_at": t, "campaign_id": extra.get("campaign_id"), "partner_code": extra.get("partner_code"),
               "referring_branch_code": extra.get("branch"), "prospecting_rm_id": extra.get("prospecting_rm"),
               "is_first_touch": not L["touches"], "created_at": t, "updated_at": None, "record_source": "synthetic_generator"}
        self.T["touch"].append(row)
        L["touches"].append(row)
        return row

    # ---------- SLA clocks
    def clock(self, L, sla_id, t, asg=None, paused_min=0, reason=None, exempt=None):
        s = self.cfg.sla[sla_id]
        pk = L["prov_key"]
        if s["basis"] == "business":
            target = self.cal.add(pk, t, s["secs"] + paused_min * 60)
            warning = self.cal.add(pk, t, s["secs"] * s["warning_pct"] // 100 + paused_min * 60)
        else:
            target = t + s["secs"] + paused_min * 60
            warning = t + s["secs"] * s["warning_pct"] // 100 + paused_min * 60
        c = {"sla_event_id": None, "lead_id": L["lead_id"], "assignment_id": asg["assignment_id"] if asg else None, "sla_id": sla_id,
             "started_at": t, "target_at": target, "warning_at": warning, "stopped_at": None, "stop_event_code": None, "sla_outcome": None,
             "paused_minutes": paused_min, "escalation_level": 0, "rule_version_id": "RV-SLA-001", "responsible_role": s["role"],
             "elapsed_business_minutes": None, "exception_reason": reason or exempt, "_exempt": exempt is not None, "_basis": s["basis"],
             "_pk": pk, "created_at": t, "updated_at": None, "record_source": "synthetic_generator"}
        self.T["sla"].append(c)
        L["clocks"].append(c)
        L.setdefault("all_clocks", []).append(c)
        return c

    def fire(self, L, event, t, asg=None):
        """Stop the lead's open clocks that list this event; assignment clocks only for their own assignment."""
        keep = []
        for c in L["clocks"]:
            s = self.cfg.sla[c["sla_id"]]
            same = c["assignment_id"] is None or asg is None or c["assignment_id"] == asg["assignment_id"]
            if event in s["stops"] and same and not (event in ("REASSIGNMENT_COMPLETED", "ASSIGNMENT_RELEASED") and c["assignment_id"] is None):
                c["stopped_at"], c["stop_event_code"], c["updated_at"] = t, event, t
            else:
                keep.append(c)
        L["clocks"] = keep

    # ---------- assignments
    def new_assignment(self, L, t, rm_id, reason, decision, role="primary", queue=None, prior=None, override=None, exception=False):
        a = {"assignment_id": self.nid("ASG", 7), "lead_id": L["lead_id"], "rm_id": rm_id, "queue_code": queue, "assignment_role": role,
             "assignment_status": "QUEUED" if rm_id is None else "ASSIGNED", "active_flag": True, "assigned_at": t,
             "assignment_reason": reason, "routing_decision_id": decision["routing_decision_id"] if decision else None,
             "rule_version_id": "RV-ROUTING-001", "prior_assignment_id": prior["assignment_id"] if prior else None,
             "prior_rm_id": prior["rm_id"] if prior else None, "acknowledged_at": None, "declined_at": None, "decline_reason": None,
             "reassignment_triggered_at": None, "reassignment_reason": None, "ended_at": None, "manual_override_flag": override is not None,
             "override_reason_code": override[0] if override else None, "override_user_role": override[1] if override else None,
             "capacity_exception_flag": exception, "created_at": t, "updated_at": t, "record_source": "synthetic_generator"}
        self.T["assignment"].append(a)
        L["open_asg"].append(a)
        L["asg_count"] += 1 if role == "primary" and rm_id else 0
        if role == "primary":
            L.setdefault("primary_ids", set()).add(a["assignment_id"])
        if rm_id:
            r = self.rms[rm_id]
            (r["active"] if role == "primary" else r["support"]).add(L["lead_id"])
            r["last_assigned"] = t
            if exception:
                r["exception_until"] = self.cal.add(None, t, self.bsecs(self.cfg.exception_days))
            if role == "primary":
                self.fire(L, "ASSIGNMENT_CREATED", t, a)
                if L["status"] == "READY_FOR_ASSIGNMENT":
                    self.status(L, "ASSIGNED", t)
        return a

    def end_assignment(self, a, t, status):
        a["assignment_status"], a["active_flag"], a["ended_at"], a["updated_at"] = status, False, t, t
        L = self.leads[a["lead_id"]]
        if a in L["open_asg"]:
            L["open_asg"].remove(a)
        if a["rm_id"]:
            r = self.rms[a["rm_id"]]
            (r["active"] if a["assignment_role"] == "primary" else r["support"]).discard(L["lead_id"])

    def activity(self, L, asg, t, typ, outcome, event, next_due=None):
        self.T["activity"].append({"activity_id": self.nid("ACT", 7), "lead_id": L["lead_id"], "assignment_id": asg["assignment_id"],
                                   "rm_id": asg["rm_id"], "activity_at": t, "activity_type": typ, "activity_outcome": outcome,
                                   "counts_as_contact_attempt": typ in ("call_attempt", "email_attempt", "conversation", "meeting"),
                                   "next_contact_due_at": next_due, "created_at": t, "updated_at": None, "record_source": "synthetic_generator"})
        L["last_activity"] = t
        self.fire(L, event, t, asg)

    # ---------- routing (replays the documented decision table; private to the generator)
    def eligible(self, rid, t, exclude):
        r = self.rms[rid]
        sn = getattr(self, "snap", {}).get(rid)
        if rid in exclude or not self.employed(r, t) or sn is None:
            return False, "excluded_other" if rid in exclude else "unavailable"
        if sn["avail"] in ("planned_leave", "unavailable"):
            return False, "unavailable"
        if sn["ratio"] is None or sn["ratio"] >= self.cfg.hard:
            return False, "hard_limit"
        return True, None

    def route(self, L, t, dtype, exclude, prior=None):
        cfg = self.cfg
        trace = ["RT-E01", "RT-E02", "RT-E03", "RT-E04", "RT-E05", "RT-E06"]
        need = L["spec_need"]
        dec = {"routing_decision_id": self.nid("RTD", 7), "lead_id": L["lead_id"], "decided_at": t, "lead_status_at_decision": L["status"],
               "decision_type": dtype, "routing_outcome": None, "selected_rm_id": None, "candidate_count": 0, "excluded_rm_count": 0,
               "territory_match_flag": None, "segment_match_flag": None, "specialist_match_flag": None, "language_match_flag": None,
               "capacity_utilization_at_decision": None, "tie_break_applied_flag": False, "rule_trace": None, "manual_review_reason": None,
               "market_scenario_id": "balanced" if L["market_flag"] == "full_framework" else None, "rule_version_id": "RV-ROUTING-001",
               "created_at": t, "updated_at": None, "record_source": "synthetic_generator"}
        self.T["decision"].append(dec)
        truth = {"routing_decision_id": dec["routing_decision_id"], "excluded_hard_limit_rm_ids": [], "boundary_exclusion": False,
                 "preferred_rm_ignored": False}
        # geography that needs a person
        if L["territory_id"] is None and dtype != "queue_release":
            dec["routing_outcome"], dec["manual_review_reason"] = "queued_manual_review", L["review_reason"]
            dec["rule_trace"] = ";".join(trace + ["RT-F03"])
            self.decision_truth.append(truth)
            return dec, None
        if L["territory_id"] is None:
            L["territory_id"] = L["review_territory"]
        tid = L["territory_id"]
        if dtype == "override":
            return self.override_place(L, dec, truth, t, exclude, trace, None, manager=True)
        pool_stages = []
        if dtype == "initial" and L["channel"] == "CH-RMP" and L["prospecting_rm"]:
            ok, _ = self.eligible(L["prospecting_rm"], t, exclude)
            covers = any(tt == tid and role == "primary" for tt, role in self.rms[L["prospecting_rm"]]["cover"])
            if ok and covers:
                return self._select(L, dec, truth, [L["prospecting_rm"]], t, trace, "self_assigned", 0)
        if need == "mandatory":
            trace.append("RT-P03")
            regional = [rid for rid, role in self.cover_index.get(tid, []) if self.rms[rid]["dedicated"] == L["industry"]]
            national = [rid for rid, role in self.cover_index.get("TER-NATIONAL-SPECIALIST", []) if self.rms[rid]["dedicated"] == L["industry"]]
            pool_stages = [regional, national]
            fallback = "queued_specialist"
        else:
            gen = [(rid, role) for rid, role in self.cover_index.get(tid, []) if self.rms[rid]["dedicated"] is None]
            pool_stages = [[r for r, role in gen if role == "primary"], [r for r, role in gen if role == "backup"]]
            fallback = "queued_national" if self.terr[tid]["type"] in ("remote_service",) else "queued_regional"
        trace += ["RT-P01", "RT-P02"] + (["RT-P04"] if L["language"] in ("en", "fr") else []) + (["RT-P05"] if L["preferred_rm"] else [])
        excluded = 0
        for stage in pool_stages:
            elig = []
            for rid in stage:
                ok, why = self.eligible(rid, t, exclude)
                if ok:
                    elig.append(rid)
                elif self.employed(self.rms[rid], t):
                    excluded += 1
                    if why == "hard_limit":
                        truth["excluded_hard_limit_rm_ids"].append(rid)
                        if self.snap[rid]["ratio"] == self.cfg.hard:
                            truth["boundary_exclusion"] = True
            if elig:
                return self._select(L, dec, truth, elig, t, trace + ["RT-X01", "RT-X02", "RT-X03", "RT-L01", "RT-L02"], "assigned_rm", excluded)
        dec["excluded_rm_count"] = excluded
        if dtype == "queue_release" or (dtype in ("reassignment",) and L["status"] != "ASSIGNED"):
            return self.override_place(L, dec, truth, t, exclude, trace, "capacity_rebalancing")
        dec["routing_outcome"] = fallback
        dec["rule_trace"] = ";".join(trace + ["RT-X01", "RT-X02", "RT-X03", "RT-F02" if fallback != "queued_regional" else "RT-F01"])
        self.decision_truth.append(truth)
        return dec, None

    def _select(self, L, dec, truth, elig, t, trace, outcome, excluded):
        pref = L["preferred_rm"]
        if pref is not None:
            if pref in elig and self.snap[pref]["ratio"] < self.cfg.warning:
                elig = [pref]
            else:
                truth["preferred_rm_ignored"] = True

        def key(rid):
            r = self.rms[rid]
            seg = (r["segment"] == "small_business") == (L["band"] in MICRO)
            spec = r["secondary"] == L["industry"] and L["spec_need"] == "preferred"
            lang = L["language"] in r["lang"].split("|") if L["language"] in ("en", "fr") else False
            return (not seg, not spec, not lang)
        best = min(key(r) for r in elig)
        tier = [r for r in elig if key(r) == best]
        low = min(self.snap[r]["ratio"] for r in tier)
        tied = [r for r in tier if self.snap[r]["ratio"] == low]
        rid = sorted(tied, key=lambda r: (self.rms[r]["last_assigned"], r))[0]
        self._fill(L, dec, rid, outcome, excluded, len(elig), len(tied) > 1, trace)
        self.decision_truth.append(truth)
        return dec, rid

    def _fill(self, L, dec, rid, outcome, excluded, ncand, tie, trace):
        r = self.rms[rid]
        tid = L["territory_id"]
        dec.update({"routing_outcome": outcome, "selected_rm_id": rid, "candidate_count": ncand, "excluded_rm_count": excluded,
                    "territory_match_flag": any(tt == tid and role == "primary" for tt, role in r["cover"]) or r["national"],
                    "segment_match_flag": (r["segment"] == "small_business") == (L["band"] in MICRO),
                    "specialist_match_flag": None if L["spec_need"] is None else (L["industry"] in (r["dedicated"], r["secondary"])),
                    "language_match_flag": (L["language"] in r["lang"].split("|")) if L["language"] in ("en", "fr") else None,
                    "capacity_utilization_at_decision": self.snap[rid]["ratio"], "tie_break_applied_flag": tie, "rule_trace": ";".join(trace)})

    def override_place(self, L, dec, truth, t, exclude, trace, reason, manager=False):
        tid = L["territory_id"]
        reg = self.terr[tid]["region"]
        pool = [rid for rid, role in self.cover_index.get(tid, [])] + sorted(
            rid for rid, r in self.rms.items() if r["dedicated"] is None and self.terr[r["home"]]["region"] == reg)
        if L["spec_need"] == "mandatory":
            pool = [rid for rid, r in sorted(self.rms.items()) if r["dedicated"] == L["industry"]] + pool
        cands = []
        for rid in dict.fromkeys(pool):
            sn = getattr(self, "snap", {}).get(rid)
            if rid in exclude or not self.employed(self.rms[rid], t) or sn is None or sn["ratio"] is None or sn["avail"] in ("planned_leave", "unavailable"):
                continue
            if sn["ratio"] <= self.cfg.exception_ceiling:
                cands.append(rid)
        if not cands:
            cands = sorted(rid for rid, r in self.rms.items() if r["dedicated"] is None and self.employed(r, t)
                           and getattr(self, "snap", {}).get(rid) and self.snap[rid]["ratio"] is not None)
        if manager:
            over = [r for r in cands if self.snap[r]["ratio"] >= self.cfg.hard]
            pick = over if (over and L["rng"].random() < self.cfg.f("override_capacity_exception_share")) else cands
            rid = pick[int(L["rng"].integers(len(pick)))]
        else:
            rid = min(cands, key=lambda r: (self.snap[r]["ratio"], r))
        self._fill(L, dec, rid, "override_assigned", dec["excluded_rm_count"], len(cands), False, trace + ["RT-O01"])
        self.decision_truth.append(truth)
        return dec, (rid, reason, self.snap[rid]["ratio"] >= self.cfg.hard)

    # ---------- engine loop
    def run(self, arrivals):
        self.closed_pool, self.reopened = [], set()
        for a in arrivals:
            self.push(a["t"], 2, "arrival", a)
        cfg = self.cfg
        n_reopen = int(round(cfg.f("reopening_share") * len(arrivals)))
        rr = np.random.default_rng([cfg.seed, 3])
        for t in sorted(int(x) for x in rr.integers(cfg.start + 70 * DAY, cfg.cutoff - 20 * DAY, n_reopen)):
            self.push(t, 2, "reopen", None)
        while self.heap:
            t, prio, _, kind, data = heapq.heappop(self.heap)
            if t >= cfg.cutoff:
                break
            self.now = t
            if kind == "snapshot":
                self.on_snapshot(data)
            elif kind == "rm_away":
                self.on_rm_away(t, *data)
            elif kind == "arrival":
                self.on_arrival(data)
            elif kind == "reopen":
                self.on_reopen(t, rr)
            elif kind == "wake":
                lid, token = data
                L = self.leads[lid]
                if L["token"] == token:
                    self.step(L, None)
            elif kind == "interrupt":
                lid, aid, reason = data
                L = self.leads[lid]
                if L["gen"] is not None and any(a["assignment_id"] == aid and a["rm_id"] and a["assignment_role"] == "primary"
                                                and a["assignment_status"] in ("ASSIGNED", "ACKNOWLEDGED") for a in L["open_asg"])                         and L["status"] not in cfg.terminal and not L.get("in_reassignment"):
                    L["token"] += 1
                    self.step(L, Interrupt(reason))

    def step(self, L, value):
        gen = L["gen"]
        try:
            instr = gen.throw(value) if isinstance(value, Interrupt) else gen.send(value)
            while instr[0] == "route":
                instr = gen.send(self.route(L, self.now, instr[1], instr[2]))
            if instr[1] < self.now:
                raise RuntimeError(f"{L['lead_id']}: sleep into the past ({instr[1]} < {self.now})")
            L["token"] += 1
            self.push(instr[1], 2, "wake", (L["lead_id"], L["token"]))
        except StopIteration:
            L["gen"] = None

    def on_rm_away(self, t, rid, kind):
        r = self.rms[rid]
        for lid in sorted(r["active"]):
            L = self.leads[lid]
            if kind != "leaver" and self.cfg.p["leave_reassigns_uncontacted_only"] == "true" and L["status"] != "ASSIGNED":
                continue
            a = next((a for a in L["open_asg"] if a["rm_id"] == rid and a["assignment_role"] == "primary"), None)
            if a and a["assignment_status"] in ("ASSIGNED", "ACKNOWLEDGED"):
                self.push(t, 2, "interrupt", (lid, a["assignment_id"], "rm_unavailable"))

    def on_reopen(self, t, rr):
        pool = sorted(l for l in set(self.closed_pool) if l not in self.reopened and self.leads[l]["status"] in ("CLOSED_LOST", "WITHDRAWN")
                      and 7 * DAY <= t - self.leads[l]["closed_at"] <= 170 * DAY and self.leads[l]["gen"] is None and self.leads[l]["asg_count"] > 0)
        if not pool:
            return
        L = self.leads[pool[int(rr.integers(len(pool)))]]
        self.reopened.add(L["lead_id"])
        t = self.biz_time(L, t)
        L["gen"] = reopen_process(self, L, t)
        L["token"] += 1
        self.push(t, 2, "wake", (L["lead_id"], L["token"]))
        L["gen"].send(None)

    def biz_time(self, L, t):
        """Move t into the lead's business hours (people act in business hours)."""
        return self.cal.add(L["prov_key"], t, 60) if self.cal.between(L["prov_key"], t, t + 1) == 0 else t

    # ---------- lead creation
    def on_arrival(self, a):
        cfg, rng = self.cfg, a["rng"]
        t = a["t"]
        kind = a["kind"]
        matched = None
        if kind in ("exact", "high", "review"):
            pool = sorted(l for l, L in self.leads.items() if L["status"] in ACTIVE_LEAD and L["status"] != "READY_FOR_ASSIGNMENT"
                          and L["captured_at"] <= t - DAY and L["prospect"]["country"] == "CA")
            if pool:
                matched = self.leads[pool[int(rng.integers(len(pool)))]]
            else:
                kind = "fresh"
        if kind == "repeat":
            pool = sorted(pid for pid, P in self.prospects.items() if P["leads"] and not P["existing"] and P["country"] == "CA"
                          and all(self.leads[l]["status"] in ("CLOSED_LOST", "WITHDRAWN") and t - self.leads[l]["closed_at"] > 30 * DAY
                                  and l not in self.reopened for l in P["leads"]))
            if pool:
                P = self.prospects[pool[int(rng.integers(len(pool)))]]
            else:
                kind = "fresh"
        if kind == "fresh":
            P = self.new_prospect(a, rng)
        elif kind == "exact":
            P = matched["prospect"]
        elif kind in ("high", "review"):
            P = self.dup_prospect(matched, kind, a, rng)
        L = self.new_lead(a, P, kind, matched, rng)
        L["gen"] = lead_process(self, L)
        L["gen"].send(None)
        self.step(L, None)

    def new_prospect(self, a, rng, base=None):
        cfg = self.cfg
        n = len(self.prospects) + 1
        pid = fmt_id("PRS", n, 6)
        geo = a["geo"]
        country = "other" if geo == "out_of_footprint" else "CA"
        P = {"prospect_id": pid, "synthetic_business_id": fmt_id("SYN-BIZ", n, 6), "name_no": n,
             "legal_name": f"Synthetic Business {n:06d} {choice(rng, ['Ltd.', 'Inc.', 'Corp.', 'Limited'])}",
             "operating_name": f"Synthetic Trading Name {n:06d}" if rng.random() < 0.3 else None,
             "band": a["band"], "industry": a["industry"], "country": country, "existing": a["existing"],
             "geo": geo, "cma": a["cma"], "prov": a["prov"], "fsa": a["fsa"], "leads": [], "created_at": a["t"],
             "phone": f"+1-{AREA_CODE.get(a['prov'], '613')}-555-01{int(rng.integers(0, 100)):02d}",
             "email": f"contact@synthetic-business-{n:06d}.example", "twin_of": None}
        if a.get("similar_to") and self.prospects:
            other = self.prospects[a["similar_to_pid"]] if a.get("similar_to_pid") in self.prospects else None
            if other and other["prov"] != P["prov"] and other["band"] != P["band"]:
                P["legal_name"] = f"Synthetic Business {other['name_no']:06d} {choice(rng, ['Inc.', 'Holdings Ltd.'])}"
                self.prospect_truth.append({"prospect_id": pid, "relationship": "similar_name_non_match", "related_prospect_id": other["prospect_id"],
                                            "rule_expected": "DUP-09;DUP-10"})
        self.prospects[pid] = P
        return P

    def dup_prospect(self, M, kind, a, rng):
        base = M["prospect"]
        n = len(self.prospects) + 1
        pid = fmt_id("PRS", n, 6)
        P = dict(base)
        P.update({"prospect_id": pid, "synthetic_business_id": fmt_id("SYN-BIZ", n, 6), "leads": [], "created_at": a["t"], "twin_of": base["prospect_id"],
                  "operating_name": None})
        if kind == "high":
            P["legal_name"] = base["legal_name"].upper().replace(".", "").replace(",", "")
            rule = "DUP-03" if rng.random() < 0.6 else "DUP-04"
        else:
            if rng.random() < 0.6 and base["prov"] and len(self.fsa_of_prov.get(base["prov"], [])) > 0:
                rule = "DUP-05"
                P["legal_name"] = base["legal_name"].replace("Ltd.", "Limited").replace("Inc.", "Incorporated")
                cand = [f for f in self.fsa_of_prov[base["prov"]] + [f for f, p in self.fsa_of_cma.get(base["cma"], []) if p == base["prov"]] if f != base["fsa"]]
                P["fsa"] = choice(rng, sorted(cand))
                P["cma"] = next((c for c, fs in self.fsa_of_cma.items() for f, p in fs if f == P["fsa"]), None)
                P["geo"] = "resolved_cma" if P["cma"] else "resolved_province_remainder"
            else:
                rule = "DUP-06"
                P["legal_name"] = f"Synthetic Operating Company {n:06d} Ltd."
        P["dup_rule"] = rule
        self.prospects[pid] = P
        return P

    def new_lead(self, a, P, kind, matched, rng):
        cfg = self.cfg
        lid = fmt_id("LED", len(self.leads) + 1, 7)
        prov = P["prov"]
        cma = P["cma"]
        tier = self.cma[cma]["tier"] if cma else None
        L = {"lead_id": lid, "prospect": P, "captured_at": a["t"], "channel": a["channel"], "kind": kind, "matched": matched, "status": None,
             "history": [], "touches": [], "clocks": [], "open_asg": [], "asg_count": 0, "opp": None, "gen": None, "token": 0, "closed_at": None,
             "nurture_due": None, "last_activity": None, "disposition": None, "updated_at": a["t"], "rng": rng,
             "prov_key": prov if prov in PROVINCES else None, "band": P["band"], "industry": P["industry"],
             "language": a["language"], "interest": a["interest"], "contact_role": a["contact_role"], "prospecting_rm": a["prospecting_rm"],
             "preferred_rm": a["preferred_rm"], "pending": a["pending"] if kind in ("fresh", "repeat") else None, "exempt": a["exempt"],
             "review_reason": None, "review_territory": None, "territory_id": None, "dup_group": None, "surviving": None,
             "market_flag": "full_framework" if tier in (1, 2) else ("scale_only_limited_evidence" if tier == 3 else "no_market_context"),
             "latent": float(rng.normal(0, cfg.f("latent_engagement_sd"))), "scen": set(), "issues": [], "fc_breach": False}
        L["spec_need"] = self.spec_need(L)
        P["leads"].append(lid)
        self.leads[lid] = L
        if kind in ("exact", "high", "review"):
            L["pending"] = None
        if kind == "repeat" and L["pending"] and L["pending"][0] in ("missing_employee_band", "missing_industry", "missing_fsa"):
            L["pending"] = None
        return L

    def spec_need(self, L):
        r = self.cfg.spc.get(L["industry"])
        if r is None:
            return None
        if r["requirement_level"] == "mandatory":
            return "mandatory"
        if r["requirement_level"] == "preferred":
            if L["industry"] in ("31-33", "23") and L["band"] in MICRO:
                return None
            return "preferred"
        return None

    def resolve_territory(self, L):
        P = L["prospect"]
        if P["geo"] == "resolved_cma":
            return f"TER-CMA-{P['cma']}"
        if P["geo"] == "resolved_province_remainder":
            if P["prov"] in ("60", "61", "62"):
                return "TER-REMOTE-NORTH"
            return f"TER-PRV-{P['prov']}"
        return None


# ============================================================ lead processes (generators)
def sleep(t):
    yield ("sleep", t)


def lead_process(E, L):
    yield None  # primed
    cfg, cal, rng = E.cfg, E.cal, L["rng"]
    t = L["captured_at"]
    E.status(L, "NEW", t)
    E.touch(L, t, L["channel"], L["touch_extra"] if "touch_extra" in L else E_touch_extra(E, L))
    exempt = "system_outage_window" if L["exempt"] else None
    E.clock(L, "SLA-01", t, exempt=exempt)
    tv = t + int(rng.integers(60, 900))
    yield from sleep(tv)
    P = L["prospect"]
    if L["pending"]:
        reason, outcome = L["pending"]
        E.fire(L, "VALIDATION_FAILED", tv)
        E.status(L, "VALIDATION_PENDING", tv, reason=reason)
        c = E.clock(L, "SLA-02", tv)
        late = rng.random() < cfg.f("sla02_breach_probability")
        if outcome == "manual_review":
            tc = c["target_at"] + int(rng.integers(600, 7200))
            yield from sleep(tc)
            L["review_reason"] = "missing_geography"
            L["review_territory"] = E.review_territory(L, rng)
            E.status(L, "READY_FOR_ASSIGNMENT", tc)
            E.fire(L, "LEAD_ROUTABLE", tc)
            L["scen"].add("SC-06")
            yield from routed_life(E, L, tc)
            return
        tt = E.badd(L, tv, E.bsecs(rng.uniform(0.1, 2.8)) if not late else E.bsecs(3) + E.bsecs(rng.uniform(0.1, 2.0)))
        yield from sleep(tt)
        if outcome == "invalid":
            E.fire(L, "LEAD_INVALIDATED", tt)
            E.status(L, "INVALID", tt, disposition="DSP-INVALID-DATA")
            L["scen"].add("SC-07")
            return
        if outcome == "withdrawn":
            E.fire(L, "PROSPECT_WITHDREW", tt)
            E.status(L, "WITHDRAWN", tt, disposition="DSP-PROSPECT-WITHDREW")
            return
        E.fire(L, "DATA_CORRECTED", tt)
        E.apply_correction(L, reason)
        L["correction_pending_ready"] = True
        tv = tt
    else:
        E.fire(L, "VALIDATION_PASSED", tv)
    if L["kind"] in ("exact", "high", "review"):
        M = L["matched"]
        tier = {"exact": "exact", "high": "high_confidence", "review": "review"}[L["kind"]]
        rule = ("DUP-02" if L["channel"] in ("CH-BRN", "CH-RMP") else "DUP-01") if tier == "exact" else P["dup_rule"]
        g = {"duplicate_group_id": E.nid("DUPG", 6), "match_rule_id": rule, "match_tier": tier, "review_decision": None, "surviving_lead_id": None,
             "member_count": 2, "flagged_at": tv, "resolved_at": None, "matched_lead_id": M["lead_id"], "created_at": tv, "updated_at": None,
             "record_source": "synthetic_generator"}
        E.T["dup"].append(g)
        L["dup_group"] = g["duplicate_group_id"]
        if tier != "review":
            g["surviving_lead_id"], g["resolved_at"], g["updated_at"] = M["lead_id"], tv, tv
            E.merge(L, M, tv)
            E.fire(L, "DUPLICATE_CONFIRMED", tv)
            E.status(L, "MERGED", tv, disposition="DSP-DUPLICATE-MERGED")
            L["scen"].add("SC-08")
            return
        E.fire(L, "PROBABLE_DUPLICATE_FLAGGED", tv)
        E.status(L, "DUPLICATE_REVIEW", tv)
        c = E.clock(L, "SLA-03", tv)
        late = rng.random() < cfg.f("sla03_breach_probability")
        tr = E.badd(L, tv, E.bsecs(rng.uniform(0.05, 0.95)) if not late else E.bsecs(rng.uniform(1.05, 2.0)))
        yield from sleep(tr)
        g["resolved_at"] = g["updated_at"] = tr
        if rng.random() < cfg.f("review_confirm_probability"):
            g["review_decision"], g["surviving_lead_id"] = "confirmed", M["lead_id"]
            E.merge(L, M, tr)
            E.fire(L, "DUPLICATE_CONFIRMED", tr)
            E.status(L, "MERGED", tr, disposition="DSP-DUPLICATE-MERGED")
            L["scen"].add("SC-09")
            return
        g["review_decision"] = "rejected"
        L["dup_rejected"] = True
        E.fire(L, "DUPLICATE_REJECTED", tr)
        tv = tr
    inel = E.ineligibility(L)
    if inel:
        E.fire(L, "LEAD_INVALIDATED", tv)
        E.status(L, "INVALID", tv, disposition=inel)
        L["scen"].add("SC-07")
        return
    if P["geo"] == "conflict":
        L["review_reason"] = "geography_conflict"
        L["review_territory"] = f"TER-CMA-{P['cma']}"
    else:
        L["territory_id"] = E.resolve_territory(L)
        if L["territory_id"] is None:
            L["review_reason"] = "missing_geography"
            L["review_territory"] = E.review_territory(L, rng)
    E.status(L, "READY_FOR_ASSIGNMENT", tv)
    E.fire(L, "LEAD_ROUTABLE", tv)
    if L.get("correction_pending_ready"):
        L["scen"].add("SC-06")
    yield from routed_life(E, L, tv)


def E_touch_extra(E, L):
    return L.get("touch_extra", {})


def reopen_process(E, L, t):
    yield None
    rng = L["rng"]
    frm = L["status"]
    reason = "prospect_re_requested_contact" if frm == "CLOSED_LOST" else "renewed_consent"
    E.status(L, "READY_FOR_ASSIGNMENT", t, reason=reason, approver=choice(rng, ["Regional Sales Manager", "Director, Business Banking Sales Strategy"], [0.8, 0.2]),
             actor="Regional Sales Manager")
    E.fire(L, "LEAD_ROUTABLE", t)
    E.touch(L, t, "CH-DIG", {})
    L["scen"].add("SC-33")
    L["reopened"] = True
    L["path"], L["opp_close"] = None, None
    yield from routed_life(E, L, t)


def routed_life(E, L, t):
    cfg, rng = E.cfg, L["rng"]
    L["territory_id"] = L["territory_id"] or None
    sla04 = cfg.manual_review_sla if L["territory_id"] is None else cfg.assign_sla[L["channel"]]
    E.clock(L, sla04, t)
    asg = yield from obtain_assignment(E, L, "initial", set(), None)
    if asg is None:
        return
    decliners = set()
    while True:
        res = yield from assignment_segment(E, L, asg)
        if res is None or res[0] == "terminal":
            return
        reason = res[1]
        tt = E.now
        old = asg
        if reason == "decline":
            decliners.add(old["rm_id"])
        else:
            old["assignment_status"], old["reassignment_triggered_at"], old["reassignment_reason"], old["updated_at"] = "REASSIGNMENT_PENDING", tt, reason, tt
        L["in_reassignment"] = True
        E.fire(L, "REASSIGNMENT_TRIGGERED", tt, old)
        if reason == "rm_unavailable":
            L["scen"].add("SC-12")
        c09 = E.clock(L, "SLA-09", tt, old)
        late = rng.random() < cfg.f("sla09_breach_probability")
        if reason == "decline":
            tr = tt  # a declined lead is rerouted at once so it is never without an owner (BR-001)
        else:
            tr = E.badd(L, tt, int(rng.uniform(0.1, 0.95) * cfg.sla["SLA-09"]["secs"]) if not late else cfg.sla["SLA-09"]["secs"] + E.bsecs(rng.uniform(0.05, 0.6)))
            yield from sleep(tr)
        if reason == "manager_decision":
            dec, rid = yield ("route", "override", decliners | {old["rm_id"]})
            asg = E.place_override(L, tr, old, dec, rid)
        else:
            dec, rid = yield ("route", "reassignment", decliners | {old["rm_id"]})
            if rid is None:
                # no eligible RM: release to a queue (only possible while ASSIGNED)
                E.end_assignment(old, tr, "REASSIGNED")
                E.fire(L, "ASSIGNMENT_RELEASED", tr, old)
                E.status(L, "READY_FOR_ASSIGNMENT", tr, reason="no_eligible_rm")
                q = E.new_assignment(L, tr, None, "queue_fallback", dec, queue=E.queue_code(dec))
                L["scen"].add("SC-10")
                L["in_reassignment"] = False
                asg = yield from obtain_assignment(E, L, "queue_release", decliners, q)
                if asg is None:
                    return
                continue
            if isinstance(rid, tuple):
                asg = E.place_override(L, tr, old, dec, rid)
            else:
                asg = E.new_assignment(L, tr, rid, "reassignment", dec, prior=old)
                E.after_assign(L, asg, dec, tr)
        if reason != "decline":
            E.end_assignment(old, tr, "REASSIGNED")
        E.fire(L, "REASSIGNMENT_COMPLETED", tr, old)
        L["in_reassignment"] = False
        L["issues"].append(reason)
        if reason in ("acknowledgement_breach", "first_contact_breach", "inactivity"):
            L["scen"].add("SC-20")


def obtain_assignment(E, L, dtype, exclude, queue_row):
    cfg, rng = E.cfg, L["rng"]
    while True:
        if queue_row is None:
            dec, rid = yield ("route", dtype, exclude)
            t = E.now
            if rid is not None:
                if isinstance(rid, tuple):
                    return E.place_override(L, t, None, dec, rid)
                reason = "self_assignment" if dec["routing_outcome"] == "self_assigned" else "routing_rule"
                a = E.new_assignment(L, t, rid, reason, dec)
                E.after_assign(L, a, dec, t)
                return a
            queue_row = E.new_assignment(L, t, None, "queue_fallback", dec, queue=E.queue_code(dec))
            E.fire(L, "QUEUE_ASSIGNED", t, queue_row)
            L["scen"].add("SC-10")
        wait = rng.exponential(cfg.f("queue_resolution_business_hours_mean")) * 3600
        tq = E.badd(L, E.now, max(900, int(wait)))
        yield from sleep(tq)
        dec, rid = yield ("route", "queue_release", exclude)
        E.end_assignment(queue_row, tq, "CANCELLED")
        if isinstance(rid, tuple):
            return E.place_override(L, tq, None, dec, rid)
        a = E.new_assignment(L, tq, rid, "routing_rule", dec)
        E.after_assign(L, a, dec, tq)
        return a


def assignment_segment(E, L, asg):
    cfg, rng = E.cfg, L["rng"]
    ta = asg["assigned_at"]
    c05 = E.clock(L, "SLA-05", ta, asg)
    fc = None
    if L["status"] == "ASSIGNED":
        pause = 0
        if rng.random() < cfg.f("first_contact_pause_probability"):
            pause = int(rng.integers(240, 1500))
        fc = E.clock(L, cfg.fc_sla.get(L["channel"], cfg.priority_sla), ta, asg, paused_min=pause,
                     reason="prospect_requested_later_date" if pause else None)
        if pause:
            L["paused_fc"] = True
    try:
        if rng.random() < cfg.f("decline_probability"):
            td = E.badd(L, ta, int(rng.uniform(0.1, 0.9) * cfg.sla["SLA-05"]["secs"]))
            yield from sleep(td)
            asg["declined_at"], asg["decline_reason"] = td, choice(rng, ["capacity", "conflict", "wrong_territory", "wrong_segment", "other"])
            asg["reassignment_triggered_at"], asg["reassignment_reason"] = td, "decline"
            E.end_assignment(asg, td, "DECLINED")
            E.fire(L, "ASSIGNMENT_DECLINED", td, asg)
            return ("reassign", "decline")
        if rng.random() < cfg.f("ack_breach_probability"):
            tb = c05["target_at"] + int(rng.integers(60, 1800))
            yield from sleep(tb)
            return ("reassign", "acknowledgement_breach")
        tk = E.badd(L, ta, min(int(rng.exponential(cfg.f("ack_delay_business_minutes_mean") * 60)) + 60, cfg.sla["SLA-05"]["secs"] - 60))
        yield from sleep(tk)
        asg["acknowledged_at"], asg["assignment_status"], asg["updated_at"] = tk, "ACKNOWLEDGED", tk
        E.fire(L, "ASSIGNMENT_ACKNOWLEDGED", tk, asg)
        if not L.get("override_done") and rng.random() < cfg.f("override_probability"):
            L["override_done"] = True
            E.push(E.badd(L, tk, E.bsecs(rng.uniform(0.5, 4))), 2, "interrupt", (L["lead_id"], asg["assignment_id"], "manager_decision"))
        while True:
            st = L["status"]
            if st == "ASSIGNED":
                r = yield from contact_phase(E, L, asg, fc)
            elif st == "CONTACTED":
                r = yield from contacted_phase(E, L, asg)
            elif st == "NURTURING":
                r = yield from nurture_phase(E, L, asg)
            elif st == "QUALIFIED":
                r = yield from opportunity_phase(E, L, asg)
            else:
                return ("terminal",)
            if r is not None:
                return r
    except Interrupt as e:
        return ("reassign", e.reason)


def contact_phase(E, L, asg, fc):
    cfg, rng = E.cfg, L["rng"]
    ta = E.now
    if rng.random() < cfg.f("withdraw_before_contact_probability"):
        tw = E.badd(L, ta, E.bsecs(rng.uniform(0.3, 1.5)))
        yield from sleep(tw)
        E.fire(L, "PROSPECT_WITHDREW", tw)
        E.status(L, "WITHDRAWN", tw, disposition=choice(rng, ["DSP-PROSPECT-WITHDREW", "DSP-CONSENT-WITHDRAWN"]))
        L["scen"].add("SC-26")
        return None
    target = fc["target_at"] if fc else E.badd(L, ta, cfg.sla[cfg.priority_sla]["secs"])
    span = max(E.cal.between(L["prov_key"], ta, target), 600)
    if rng.random() < cfg.f("first_attempt_breach_probability"):
        L["fc_breach"] = True
        if rng.random() < cfg.f("first_contact_reassign_given_breach"):
            yield from sleep(E.badd(L, target, span))
            return ("reassign", "first_contact_breach")
        t1 = E.badd(L, target, int(span * rng.uniform(0.05, 0.9)))
    else:
        t1 = E.badd(L, ta, int(span * rng.beta(1.2, 4.0)) + 60)
    p = 1 / (1 + math.exp(-(cfg.f("contact_success_logit_base") + 0.5 * L["latent"])))
    t = t1
    for k in range(cfg.i("max_contact_attempts")):
        yield from sleep(t)
        if rng.random() < p:
            E.activity(L, asg, t, choice(rng, ["conversation", "meeting"], [0.85, 0.15]), "two_way_contact", "FIRST_TWO_WAY_CONTACT")
            E.status(L, "CONTACTED", t)
            L["first_contact_at"] = t
            return None
        E.activity(L, asg, t, choice(rng, ["call_attempt", "email_attempt"], [0.7, 0.3]), choice(rng, ["no_answer", "left_message"]), "CONTACT_ATTEMPTED")
        E.clock(L, "SLA-07", t, asg)
        late = rng.random() < cfg.f("sla07_breach_probability")
        t = E.badd(L, t, E.bsecs(rng.uniform(0.2, 1.9)) if not late else E.bsecs(rng.uniform(2.1, 3.5)))
    tc = E.badd(L, t, E.bsecs(rng.uniform(0.05, 0.5)))
    yield from sleep(tc)
    E.status(L, "CLOSED_LOST", tc, disposition="DSP-NO-RESPONSE")
    L["scen"].add("SC-18")
    return None


def decide_path(E, L, allow_nurture):
    cfg, rng = E.cfg, L["rng"]
    x = cfg.f("qualification_logit_base") + L["latent"] + E.effects(L)
    q = 1 / (1 + math.exp(-x))
    u = rng.random()
    if allow_nurture and u < cfg.f("nurture_probability"):
        return "nurture"
    u = rng.random()
    if u < cfg.f("withdraw_after_contact_probability"):
        return "withdraw"
    if L["opp"] is not None:
        return "close"
    return "qualify" if rng.random() < q else "close"


def contacted_phase(E, L, asg):
    cfg, rng = E.cfg, L["rng"]
    if L.get("path") is None:
        L["path"] = decide_path(E, L, not L.get("nurtured"))
    path = L["path"]
    t = E.now
    n = int(rng.integers(1 if path == "qualify" else 0, cfg.i("contacted_followups_max") + 1))
    inactive_at = int(rng.integers(1, n + 1)) if (n >= 1 and rng.random() < cfg.f("inactivity_gap_probability")) else None
    gap_target = cfg.sla["SLA-08A"]["secs"]
    for k in range(1, n + 1):
        t = E.badd(L, t, E.bsecs(cfg.rng_range(rng, "followup_gap_business_days")))
        yield from sleep(t)
        E.activity(L, asg, t, choice(rng, ["call_attempt", "email_attempt", "meeting", "note"], [0.35, 0.25, 0.25, 0.15]),
                   choice(rng, ["two_way_contact", "completed", "meeting_held"], [0.5, 0.35, 0.15]), "FOLLOW_UP_ACTIVITY")
        E.clock(L, "SLA-08A", t, asg)
        if inactive_at == k:
            L["scen"].add("SC-19")
            tb = E.badd(L, t, gap_target)
            tr = E.badd(L, tb, E.bsecs(rng.uniform(0.3, 1.2)))
            yield from sleep(tr)
            if rng.random() < cfg.f("inactivity_reassign_given_breach"):
                return ("reassign", "inactivity")
            t = tr
            E.activity(L, asg, t, "call_attempt", "two_way_contact", "FOLLOW_UP_ACTIVITY")
            E.clock(L, "SLA-08A", t, asg)
    t = E.badd(L, t, E.bsecs(cfg.rng_range(rng, "decision_after_contact_business_days")))
    yield from sleep(t)
    if path == "qualify":
        E.activity(L, asg, t, "meeting", "meeting_held", "FOLLOW_UP_ACTIVITY")
        E.clock(L, "SLA-08A", t, asg)
        E.status(L, "QUALIFIED", t)
        E.open_opportunity(L, asg, t)
        L["scen"].add("SC-23")
    elif path == "nurture":
        due = t + int(cfg.rng_range(rng, "nurture_next_contact_days")) * DAY
        L["nurture_due"] = due
        E.activity(L, asg, t, "nurture_task", "scheduled", "NURTURE_SCHEDULED", next_due=due)
        E.status(L, "NURTURING", t)
        far = due - (t + cfg.sla["SLA-08B"]["secs"])
        pause = max(0, far // 60 + 7 * 1440) if far > 0 else 0
        E.clock(L, "SLA-08B", t, asg, paused_min=int(pause), reason="prospect_set_next_contact" if pause else None)
        L["nurtured"] = True
        L["path"] = None
    elif path == "withdraw":
        E.activity(L, asg, t, "note", "completed", "FOLLOW_UP_ACTIVITY")
        E.fire(L, "PROSPECT_WITHDREW", t)
        E.status(L, "WITHDRAWN", t, disposition=choice(rng, ["DSP-PROSPECT-WITHDREW", "DSP-CONSENT-WITHDRAWN"]))
    else:
        E.activity(L, asg, t, choice(rng, ["call_attempt", "meeting"], [0.7, 0.3]), choice(rng, ["two_way_contact", "meeting_held"], [0.8, 0.2]),
                   "FOLLOW_UP_ACTIVITY")
        E.status(L, "CLOSED_LOST", t, disposition=choice(rng, ["DSP-NOT-QUALIFIED", "DSP-NOT-INTERESTED", "DSP-LOST-COMPETITOR"], [0.5, 0.35, 0.15]))
        L["scen"].add("SC-25")
    return None


def nurture_phase(E, L, asg):
    cfg, rng = E.cfg, L["rng"]
    due = L["nurture_due"]
    t = max(E.now, due) + int(rng.integers(0, 4 * DAY))
    t = E.biz_time(L, t)
    yield from sleep(t)
    u = rng.random()
    if u < cfg.f("nurture_reengage_probability"):
        E.activity(L, asg, t, "conversation", "two_way_contact", "NURTURE_REENGAGED")
        E.status(L, "CONTACTED", t)
        L["scen"].add("SC-22")
        L["path"] = decide_path(E, L, False)
    elif u < cfg.f("nurture_reengage_probability") + 0.45:
        E.activity(L, asg, t, "note", "completed", "FOLLOW_UP_ACTIVITY")
        E.status(L, "CLOSED_LOST", t, disposition="DSP-TIMING")
    else:
        E.activity(L, asg, t, "note", "completed", "FOLLOW_UP_ACTIVITY")
        E.fire(L, "PROSPECT_WITHDREW", t)
        E.status(L, "WITHDRAWN", t, disposition="DSP-PROSPECT-WITHDREW")
    return None


def opportunity_phase(E, L, asg):
    cfg, rng = E.cfg, L["rng"]
    if L.get("opp_close") is None:
        L["opp_close"] = E.badd(L, E.now, E.bsecs(cfg.rng_range(rng, "opportunity_duration_business_days")))
        x = cfg.f("win_logit_base") + 0.5 * (L["latent"] + E.effects(L))
        L["opp_result"] = "won" if rng.random() < 1 / (1 + math.exp(-x)) else choice(rng, ["lost", "withdrawn"], [0.85, 0.15])
    t = E.now
    while True:
        nt = E.badd(L, t, E.bsecs(cfg.rng_range(rng, "opportunity_followup_gap_business_days")))
        if nt >= L["opp_close"]:
            break
        t = nt
        yield from sleep(t)
        E.activity(L, asg, t, choice(rng, ["meeting", "call_attempt", "email_attempt"], [0.4, 0.4, 0.2]),
                   choice(rng, ["meeting_held", "two_way_contact", "completed"], [0.4, 0.4, 0.2]), "FOLLOW_UP_ACTIVITY")
        E.clock(L, "SLA-08A", t, asg)
    t = max(L["opp_close"], t + 60)
    yield from sleep(t)
    E.activity(L, asg, t, "meeting", "completed", "FOLLOW_UP_ACTIVITY")
    res = L["opp_result"]
    if res == "won":
        E.status(L, "CONVERTED", t, disposition="DSP-WON")
        L["scen"].add("SC-24")
    elif res == "lost":
        E.status(L, "CLOSED_LOST", t, disposition=choice(rng, ["DSP-LOST-COMPETITOR", "DSP-NOT-INTERESTED"], [0.6, 0.4]))
    else:
        E.fire(L, "PROSPECT_WITHDREW", t)
        E.status(L, "WITHDRAWN", t, disposition="DSP-PROSPECT-WITHDREW")
    return None


# ============================================================ engine helpers used by the processes
def _effects(self, L):
    c = self.cfg
    e = {"1-4": c.f("effect_band_1-4"), "20-99": c.f("effect_band_20-99"), "100-499": c.f("effect_band_100-499")}.get(L["band"], 0.0)
    if L["channel"] in ("CH-BRN", "CH-PTR"):
        e += c.f("effect_referral_channel")
    if L["channel"] == "CH-RMP":
        e += c.f("effect_rm_prospecting")
    if L["interest"] == "credit":
        e += c.f("effect_credit_interest")
    if L["fc_breach"]:
        e += c.f("effect_first_contact_breach")
    return e + c.f("effect_market_context") * 0


def _merge(self, L, M, t):
    for tch in L["touches"]:
        tch["lead_id"], tch["is_first_touch"], tch["updated_at"] = M["lead_id"], False, t
        M["touches"].append(tch)
    L["touches"] = []
    L["surviving"] = M["lead_id"]
    if tch["channel_code"] != M["channel"]:
        M["scen"].add("SC-32")


def _ineligibility(self, L):
    P = L["prospect"]
    if P["country"] != "CA":
        return "DSP-OUT-OF-FOOTPRINT"
    if P["band"] == "500-plus":
        return "DSP-INELIGIBLE-SIZE"
    if P["industry"] == "91":
        return "DSP-INELIGIBLE-SECTOR"
    if P["existing"]:
        return "DSP-EXISTING-RELATIONSHIP"
    return None


def _apply_correction(self, L, reason):
    P, rng = L["prospect"], L["rng"]
    if reason == "missing_fsa":
        prov = P["prov"] or "35"
        cmas = [c for c in sorted(self.fsa_of_cma) if any(p == prov for _, p in self.fsa_of_cma[c])]
        if cmas and rng.random() < 0.85:
            c = choice(rng, cmas, [self.cma_w[c] for c in cmas])
            P["fsa"] = choice(rng, [f for f, p in self.fsa_of_cma[c] if p == prov])
            P["cma"], P["geo"] = c, "resolved_cma"
        else:
            P["fsa"], P["cma"], P["geo"] = choice(rng, self.fsa_of_prov[prov]), None, "resolved_province_remainder"
        P["prov"] = prov
        L["prov_key"] = prov
        tier = self.cma[P["cma"]]["tier"] if P["cma"] else None
        L["market_flag"] = "full_framework" if tier in (1, 2) else ("scale_only_limited_evidence" if tier == 3 else "no_market_context")
    elif reason == "missing_employee_band":
        P["band"] = L["band"] = choice(rng, ["1-4", "5-19", "20-99", "100-499"], [0.42, 0.36, 0.17, 0.05])
    elif reason == "missing_industry":
        P["industry"] = L["industry"] = self.draw_industry(P["cma"], rng)
        L["spec_need"] = self.spec_need(L)
    elif reason == "missing_consent":
        L["consent"] = True
    elif reason == "expired_campaign_code":
        cid = self.active_campaign(L["captured_at"], rng)
        L["touches"][0]["campaign_id"] = cid


def _review_territory(self, L, rng):
    prov = L["prospect"]["prov"] or "35"
    cmas = [c for c in sorted(self.cma) if prov in self.cma[c]["provs"]]
    return f"TER-CMA-{choice(rng, cmas)}" if cmas else f"TER-PRV-{prov}"


def _queue_code(self, dec):
    return {"queued_regional": "regional", "queued_national": "national", "queued_specialist": "specialist",
            "queued_manual_review": "manual_review"}.get(dec["routing_outcome"], "regional")


def _after_assign(self, L, a, dec, t):
    # preferred-industry fallback: generalist primary plus a specialist-support secondary assignment (SPC-03, SPC-04)
    if L["spec_need"] == "preferred" and L["industry"] in ("62", "31-33") and self.rms[a["rm_id"]]["secondary"] != L["industry"]:
        cands = [rid for rid, role in self.cover_index.get(L["territory_id"], []) if self.rms[rid]["secondary"] == L["industry"] and rid != a["rm_id"]]
        cands = [r for r in cands if self.eligible(r, t, set())[0]]
        if cands and not any(x["assignment_role"] == "specialist_support" for x in L["open_asg"]):
            rid = min(cands, key=lambda r: (self.snap[r]["ratio"], r))
            s = self.new_assignment(L, t, rid, "routing_rule", dec, role="specialist_support")
            self.clock(L, "SLA-05", t, s)
            tk = self.badd(L, t, 1800 + int(L["rng"].integers(0, 3600)))
            s["acknowledged_at"], s["assignment_status"], s["updated_at"] = tk, "ACKNOWLEDGED", tk
            self.fire_later_support(L, s, tk)
    if dec["territory_match_flag"] is False:
        L["scen"].add("SC-13")
    if L["spec_need"] is not None and (dec["specialist_match_flag"] is False or dec["routing_outcome"] == "override_assigned"
                                       or (L["spec_need"] == "mandatory" and self.rms[a["rm_id"]]["national"])):
        L["scen"].add("SC-14")
    if dec["language_match_flag"] is not None:
        L["scen"].add("SC-15")


def _fire_later_support(self, L, s, tk):
    # the support RM's acknowledgement stops its SLA-05 clock (recorded on the clock directly; no lead event interleaving)
    for c in L["clocks"]:
        if c["assignment_id"] == s["assignment_id"] and c["sla_id"] == "SLA-05":
            c["stopped_at"], c["stop_event_code"], c["updated_at"] = tk, "ASSIGNMENT_ACKNOWLEDGED", tk
    L["clocks"] = [c for c in L["clocks"] if c["stopped_at"] is None]


def _place_override(self, L, t, old, dec, rid_tuple):
    rid, reason, exception = rid_tuple
    if dec["routing_outcome"] != "override_assigned":
        dec["routing_outcome"] = "override_assigned"
        dec["rule_trace"] = (dec["rule_trace"] or "") + ";RT-O01"
    reason_code = choice(L["rng"], ["client_relationship", "specialist_need", "capacity_rebalancing", "territory_correction", "other"],
                         [0.4, 0.15, 0.2, 0.15, 0.1]) if reason is None else reason
    role = choice(L["rng"], ["Regional Sales Manager", "Director, Business Banking Sales Strategy"], [0.85, 0.15])
    a = self.new_assignment(L, t, rid, "override", dec, prior=old, override=(reason_code, role), exception=exception)
    L["scen"].add("SC-21")
    self.after_assign(L, a, dec, t)
    return a


def _open_opportunity(self, L, asg, t):
    prod = L["interest"] if L["interest"] != "not stated" else choice(L["rng"], ["operating account", "credit", "cash management", "merchant services"])
    o = {"opportunity_id": self.nid("OPP", 7), "lead_id": L["lead_id"], "rm_id": asg["rm_id"], "opened_at": t, "opportunity_status": "open",
         "primary_product": prod, "closed_at": None, "outcome_reason": None, "created_at": t, "updated_at": None, "record_source": "synthetic_generator"}
    self.T["opp"].append(o)
    L["opp"] = o


def _draw_industry(self, cma, rng):
    w = self.sectors.get(cma) or self.national_sectors
    ks = sorted(w)
    return choice(rng, ks, [w[k] for k in ks])


def _active_campaign(self, t, rng):
    d = dt.datetime.fromtimestamp(t, UTC).date()
    act = [c for c in self.T["campaign"] if c["campaign_start_date"] <= d <= c["campaign_end_date"]]
    return choice(rng, act)["campaign_id"] if act else self.T["campaign"][0]["campaign_id"]


for _n, _f in [("effects", _effects), ("merge", _merge), ("ineligibility", _ineligibility), ("apply_correction", _apply_correction),
               ("review_territory", _review_territory), ("queue_code", _queue_code), ("after_assign", _after_assign),
               ("fire_later_support", _fire_later_support), ("place_override", _place_override), ("open_opportunity", _open_opportunity),
               ("draw_industry", _draw_industry), ("active_campaign", _active_campaign)]:
    setattr(Engine, _n, _f)


# ============================================================ arrivals
def make_arrivals(E):
    cfg, cal = E.cfg, E.cal
    rng = np.random.default_rng([cfg.seed, 4])
    n = cfg.i("lead_count")
    E.national_sectors = {}
    for c, d in E.sectors.items():
        for k, v in d.items():
            E.national_sectors[k] = E.national_sectors.get(k, 0) + v
    ch_codes = ["CH-DIG", "CH-BRN", "CH-MKT", "CH-PTR", "CH-RMP"]
    ch_w = [cfg.f(f"channel_share_{c}") for c in ch_codes]
    kinds = ["exact", "high", "review", "repeat"]
    kind_w = [cfg.f("exact_duplicate_share"), cfg.f("high_confidence_duplicate_share"), cfg.f("review_duplicate_share"), cfg.f("repeat_interest_share")]
    cmas = sorted(E.cma)
    days = [(cfg.start + k * DAY) for k in range((cfg.cutoff - cfg.start) // DAY)]
    rm_ids = sorted(E.rms)
    out = []
    for i in range(n):
        r = np.random.default_rng([cfg.seed, 100, i])
        ch = choice(r, ch_codes, ch_w)
        u = r.random()
        acc, kind = 0.0, "fresh"
        for k, w in zip(kinds, kind_w):
            acc += w
            if u < acc:
                kind = k
                break
        # capture time
        while True:
            d0 = days[int(r.integers(len(days)))]
            dd = dt.datetime.fromtimestamp(d0, UTC).date()
            weekend = dd.weekday() >= 5
            if ch in ("CH-DIG", "CH-MKT"):
                if weekend and r.random() > cfg.f("weekend_capture_share_digital") * 2.5:
                    continue
                t = d0 + int(r.integers(0, DAY))
            else:
                if weekend:
                    continue
                t = None
            break
        # geography
        g = r.random()
        cut = np.cumsum([cfg.f("share_province_remainder"), cfg.f("share_remote_service"), cfg.f("share_missing_fsa"),
                         cfg.f("share_geography_conflict"), cfg.f("share_out_of_footprint")])
        cma = prov = fsa = None
        prm = None
        if ch == "CH-RMP":
            prm = choice(r, [x for x in rm_ids if not E.rms[x]["national"]])
            home = E.rms[prm]["home"]
            tt = E.terr[home]
            if tt["type"] == "cma":
                geo, cma = "resolved_cma", tt["cma"]
            elif tt["type"] == "province_remainder":
                geo, prov = "resolved_province_remainder", tt["prov"]
            else:
                geo, prov = "resolved_province_remainder", choice(r, ["60", "61", "62"])
            if E.rms[prm]["dedicated"] and not E.rms[prm]["national"]:
                reg_cmas = [c for c in cmas if E.terr[f"TER-CMA-{c}"]["region"] == E.terr[home]["region"]]
                cma = choice(r, reg_cmas, [E.cma_w[c] for c in reg_cmas])
                geo = "resolved_cma"
        elif g < cut[0]:
            geo, prov = "resolved_province_remainder", choice(r, E.remainder_provs, E.remainder_w)
        elif g < cut[1]:
            geo, prov = "resolved_province_remainder", choice(r, ["60", "61", "62"])
        elif g < cut[2]:
            geo = "missing"
            prov = choice(r, ["35", "24", "59", "48", None], [0.3, 0.2, 0.15, 0.1, 0.25])
        elif g < cut[3]:
            geo = "conflict"
            cma = choice(r, cmas, [E.cma_w[c] for c in cmas])
        elif g < cut[4]:
            geo = "out_of_footprint"
        else:
            geo, cma = "resolved_cma", choice(r, cmas, [E.cma_w[c] for c in cmas])
        if cma:
            fp = choice(r, E.fsa_of_cma[cma])
            fsa, prov = fp
            if geo == "conflict":
                prov = choice(r, [p for p in PROVINCES if p not in E.cma[cma]["provs"] and p not in ("60", "61", "62")])
        elif geo == "resolved_province_remainder":
            fsa = choice(r, E.fsa_of_prov[prov])
        pk = prov if prov in PROVINCES else None
        if t is None:
            local_day = dt.datetime.fromtimestamp(d0, UTC).date()
            if not cal.is_business_date(pk, local_day):
                local_day = local_day + dt.timedelta(days=1)
                while not cal.is_business_date(pk, local_day):
                    local_day += dt.timedelta(days=1)
            t = cal.at_local(pk, local_day, 9, 0) + int(r.integers(0, 7 * 3600 + 1800))
        t = min(max(t, cfg.start + 3600), cfg.cutoff - 3600)
        # business attributes
        band = choice(r, ["1-4", "5-19", "20-99", "100-499"], [cfg.f(f"band_share_{b}") for b in ["1-4", "5-19", "20-99", "100-499"]])
        if r.random() < cfg.f("share_500_plus"):
            band = "500-plus"
        if prm and E.rms[prm]["dedicated"]:
            industry = E.rms[prm]["dedicated"]
        elif r.random() < cfg.f("unclassified_industry_share"):
            industry = "UNCLASSIFIED"
        elif r.random() < cfg.f("share_public_administration"):
            industry = "91"
        else:
            industry = E.draw_industry(cma, r)
        existing = r.random() < cfg.f("existing_relationship_share")
        if prov == "24" or cma == "505" or prov == "13":
            lang = "fr" if r.random() < cfg.f("stated_fr_share_quebec_and_bilingual_markets") else None
        else:
            lang = "fr" if r.random() < cfg.f("stated_fr_share_elsewhere") else None
        if lang is None:
            lang = "en" if r.random() < cfg.f("stated_en_share") else "not stated"
        pending = None
        pr = r.random()
        pcut = np.cumsum([cfg.f("share_missing_employee_band"), cfg.f("share_missing_industry"), cfg.f("share_missing_consent")])
        if geo == "missing":
            pending = "missing_fsa"
        elif pr < pcut[0]:
            pending = "missing_employee_band"
        elif pr < pcut[1]:
            pending = "missing_industry"
        elif pr < pcut[2]:
            pending = "missing_consent"
        elif ch == "CH-MKT" and r.random() < cfg.f("share_expired_campaign_code"):
            pending = "expired_campaign_code"
        if pending:
            v = r.random()
            if pending == "missing_fsa" and ch in ("CH-BRN", "CH-PTR") and v > cfg.f("validation_correction_probability"):
                outcome = "manual_review"
            elif v < cfg.f("validation_correction_probability"):
                outcome = "corrected"
            elif v < cfg.f("validation_correction_probability") + cfg.f("validation_withdraw_probability"):
                outcome = "withdrawn"
            else:
                outcome = "invalid"
            if pending == "missing_employee_band" and outcome == "corrected":
                band_final = band
                band = "unknown"
            pending = (pending, outcome)
        preferred = None
        if ch in ("CH-BRN", "CH-PTR") and r.random() < cfg.f("preferred_rm_named_share"):
            same = [x for x in rm_ids if E.rms[x]["dedicated"] is None and (prov is None or REGION.get(prov) == E.terr[E.rms[x]["home"]]["region"])]
            other = [x for x in rm_ids if E.rms[x]["dedicated"] is None]
            preferred = choice(r, same if (same and r.random() < 0.8) else other)
        extra = {}
        if ch == "CH-BRN":
            extra["branch"] = f"BR-FICT-{(prov or '00')}{int(r.integers(1, 40)):02d}"
        if ch == "CH-PTR":
            extra["partner_code"] = f"PTN-FICT-{int(r.integers(1, 41)):03d}"
        if ch == "CH-RMP":
            extra["prospecting_rm"] = prm
        out.append({"i": i, "t": int(t), "channel": ch, "kind": kind, "geo": geo, "cma": cma, "prov": prov, "fsa": fsa if geo != "missing" else None,
                    "band": band, "industry": industry if not (pending and pending[0] == "missing_industry") else None, "existing": existing,
                    "language": lang, "interest": choice(r, ["operating account", "credit", "cash management", "merchant services", "not stated"],
                                                         [0.3, 0.3, 0.1, 0.1, 0.2]),
                    "contact_role": choice(r, ["owner", "finance lead", "operations lead", "other"], [0.6, 0.2, 0.1, 0.1]),
                    "prospecting_rm": prm, "preferred_rm": preferred, "pending": pending, "exempt": r.random() < cfg.f("intake_exempt_probability"),
                    "extra": extra, "similar": r.random() < cfg.f("similar_name_non_match_share")})
    out.sort(key=lambda a: (a["t"], a["i"]))
    for k, a in enumerate(out):
        a["rng"] = np.random.default_rng([cfg.seed, 200, k])
    return out


# ============================================================ assembly
def build(cfg, out_root, truth_root):
    cal = Calendar(cfg)
    E = Engine(cfg, cal, load_reference())
    E.build_reference()
    arrivals = make_arrivals(E)
    # attach touch extras and campaign draws, similar-name links, pending band restore
    orig_new_lead = E.new_lead

    def new_lead(a, P, kind, matched, rng):
        L = orig_new_lead(a, P, kind, matched, rng)
        extra = dict(a["extra"])
        if a["channel"] == "CH-MKT":
            d = dt.datetime.fromtimestamp(a["t"], UTC).date()
            if L["pending"] and L["pending"][0] == "expired_campaign_code":
                ended = [c for c in E.T["campaign"] if c["campaign_end_date"] < d]
                extra["campaign_id"] = (ended[-1] if ended else E.T["campaign"][0])["campaign_id"]
            else:
                extra["campaign_id"] = E.active_campaign(a["t"], rng)
        L["touch_extra"] = extra
        L["consent"] = not (L["pending"] and L["pending"][0] == "missing_consent")
        return L
    E.new_lead = new_lead
    orig_new_prospect = E.new_prospect

    def new_prospect(a, rng, base=None):
        if a.get("similar") and E.prospects:
            keys = sorted(E.prospects)
            a = dict(a)
            a["similar_to"] = True
            a["similar_to_pid"] = keys[int(rng.integers(len(keys)))]
        return orig_new_prospect(a, rng)
    E.new_prospect = new_prospect
    E.run(arrivals)
    E.finish()
    return E


def _finish(self):
    """Close clocks at the cut-off, add escalations, derive lead and prospect rows, scenario memberships and truth labels."""
    cfg, cal = self.cfg, self.cal
    cut = cfg.cutoff
    esc_rng = np.random.default_rng([cfg.seed, 5])
    extra = []
    for c in list(self.T["sla"]):
        # SLA clocks use the lead's final province calendar (a corrected postal prefix can change it after intake)
        L = self.leads[c["lead_id"]]
        if c["_pk"] != L["prov_key"]:
            s_ = cfg.sla[c["sla_id"]]
            c["_pk"] = L["prov_key"]
            if s_["basis"] == "business":
                c["target_at"] = cal.add(c["_pk"], c["started_at"], s_["secs"] + c["paused_minutes"] * 60)
                c["warning_at"] = cal.add(c["_pk"], c["started_at"], s_["secs"] * s_["warning_pct"] // 100 + c["paused_minutes"] * 60)
        stop = c["stopped_at"]
        end = stop if stop is not None else cut
        if c["_basis"] == "business":
            el = cal.between(c["_pk"], c["started_at"], end)
        else:
            el = end - c["started_at"]
        c["elapsed_business_minutes"] = max(0, el // 60 - c["paused_minutes"])
        if c["_exempt"]:
            c["sla_outcome"] = "not_applicable"
        elif stop is not None:
            c["sla_outcome"] = "met" if stop <= c["target_at"] else "breached_closed"
        else:
            c["sla_outcome"] = "breached_open" if cut >= c["target_at"] else ("warning_open" if cut >= c["warning_at"] else "open")
        if c["sla_outcome"] in ("breached_closed", "breached_open"):
            c["escalation_level"] = 1
            if c["sla_id"][:6] in ("SLA-04", "SLA-05", "SLA-06", "SLA-08", "SLA-09") and c["sla_id"] != "SLA-08B":
                L = self.leads[c["lead_id"]]
                tb = c["target_at"]
                e = self.clock(L, "SLA-10", tb, None)
                L["clocks"].remove(e)
                e["assignment_id"] = c["assignment_id"]
                lvl2 = esc_rng.random() < cfg.f("escalation_level2_probability")
                res = cal.add(e["_pk"], tb, int(esc_rng.uniform(0.05, 0.95) * cfg.sla["SLA-10"]["secs"]) if not lvl2
                              else cfg.sla["SLA-10"]["secs"] + int(esc_rng.uniform(0.05, 0.8) * cfg.sla["SLA-10"]["secs"]))
                if res < cut:
                    e["stopped_at"], e["stop_event_code"], e["updated_at"] = res, "ESCALATION_RESOLVED", res
                e["escalation_level"] = 1
                extra.append((e, c))
    for e, c in extra:
        stop = e["stopped_at"]
        end = stop if stop is not None else cut
        e["elapsed_business_minutes"] = max(0, cal.between(e["_pk"], e["started_at"], end) // 60)
        if stop is not None:
            e["sla_outcome"] = "met" if stop <= e["target_at"] else "breached_closed"
        else:
            e["sla_outcome"] = "breached_open" if cut >= e["target_at"] else ("warning_open" if cut >= e["warning_at"] else "open")
        if e["sla_outcome"] in ("breached_closed", "breached_open"):
            e["escalation_level"] = 2
            c["escalation_level"] = 2
    self.T["sla"].sort(key=lambda c: (c["started_at"], c["lead_id"], c["sla_id"], c["assignment_id"] or ""))
    for k, c in enumerate(self.T["sla"], 1):
        c["sla_event_id"] = fmt_id("SLE", k, 7)
    # prospects and leads
    for pid in sorted(self.prospects):
        P = self.prospects[pid]
        firsts = [self.leads[l]["captured_at"] for l in P["leads"]]
        self.T["prospect"].append({"prospect_id": pid, "synthetic_business_id": P["synthetic_business_id"], "legal_name": P["legal_name"],
                                   "normalized_legal_name": normalize(P["legal_name"]), "operating_name": P["operating_name"],
                                   "employee_band": P["band"], "industry_system": "NAICS2022_1097" if P["industry"] else None,
                                   "industry_code": P["industry"], "country_code": P["country"], "existing_relationship_flag": P["existing"],
                                   "created_at": min(firsts), "updated_at": max(firsts) if len(firsts) > 1 else None,
                                   "record_source": "synthetic_generator"})
    for lid in sorted(self.leads):
        L = self.leads[lid]
        P = L["prospect"]
        self.T["lead"].append({"lead_id": lid, "prospect_id": P["prospect_id"], "captured_at": L["captured_at"], "first_channel_code": L["channel"],
                               "contact_role": L["contact_role"], "contact_email_fictional": P["email"], "contact_phone_fictional": P["phone"],
                               "postal_fsa": P["fsa"], "province_code": P["prov"], "cma_code_sgc2021": P["cma"], "territory_id": L["territory_id"],
                               "geography_status": P["geo"], "market_evidence_flag": L["market_flag"], "stated_product_interest": L["interest"],
                               "stated_service_language": L["language"], "consent_to_contact_flag": L.get("consent", True), "priority_band": "P2",
                               "current_lead_status": L["status"], "disposition_code": L["disposition"], "duplicate_group_id": L["dup_group"],
                               "surviving_lead_id": L["surviving"], "local_time_zone": self.cal.zone[L["prov_key"]],
                               "created_at": L["captured_at"], "updated_at": L["updated_at"], "record_source": "synthetic_generator"})
        # observable-only scenario flags derived after the run
        ch = {"CH-DIG": "SC-01", "CH-BRN": "SC-02", "CH-MKT": "SC-03", "CH-PTR": "SC-04", "CH-RMP": "SC-05"}[L["channel"]]
        if L["asg_count"] > 0 and (ch != "SC-01" or ("CONTACTED" in L["history"] and not L["issues"] and not L["fc_breach"]
                                                       and "VALIDATION_PENDING" not in L["history"])):
            L["scen"].add(ch)
        if P["geo"] == "resolved_province_remainder" and L["asg_count"] > 0:
            L["scen"].add("SC-27")
        if L["market_flag"] == "scale_only_limited_evidence" and L["asg_count"] > 0:
            L["scen"].add("SC-28")
        if L["market_flag"] == "no_market_context" and L["asg_count"] > 0:
            L["scen"].add("SC-29")
        L.setdefault("all_clocks", [])
        L.setdefault("primary_ids", set())
        if L["fc_breach"] and "CONTACTED" in L["history"]:
            L["scen"].add("SC-17")
        if any(c["sla_id"] == "SLA-05" and c["sla_outcome"] in ("breached_closed", "breached_open") and c["assignment_id"] in L["primary_ids"]
               for c in L["all_clocks"]):
            L["scen"].add("SC-16")
        if L["asg_count"] >= 3:
            L["scen"].add("SC-31")
    order = ["SC-33", "SC-32", "SC-09", "SC-08", "SC-07", "SC-06", "SC-21", "SC-12", "SC-16", "SC-20", "SC-19", "SC-18", "SC-30", "SC-11",
             "SC-10", "SC-14", "SC-13", "SC-15", "SC-31", "SC-17", "SC-26", "SC-24", "SC-22", "SC-23", "SC-25", "SC-28", "SC-27", "SC-29",
             "SC-05", "SC-04", "SC-02", "SC-03", "SC-01"]
    by_dec = {d["routing_decision_id"]: d for d in self.T["decision"]}
    for tr in self.decision_truth:
        d = by_dec[tr["routing_decision_id"]]
        L = self.leads[d["lead_id"]]
        if tr["excluded_hard_limit_rm_ids"] and d["selected_rm_id"]:
            L["scen"].add("SC-11")
        if tr["boundary_exclusion"] or (d["capacity_utilization_at_decision"] is not None and d["capacity_utilization_at_decision"] == cfg.warning):
            L["scen"].add("SC-30")
        if tr["preferred_rm_ignored"]:
            L["scen"].add("SC-13")
    for lid in sorted(self.leads):
        L = self.leads[lid]
        prim = next((s for s in order if s in L["scen"]), None)
        for s in sorted(L["scen"]):
            self.membership.append({"lead_id": lid, "scenario_id": s})
        self.truth[lid] = {"lead_id": lid, "primary_scenario_id": prim, "arrival_kind": L["kind"],
                           "matched_lead_id": L["matched"]["lead_id"] if L["matched"] else None,
                           "expected_duplicate_outcome": {"exact": "auto_merge", "high": "auto_merge", "review": "review_confirmed" if L["status"] == "MERGED" else "review_rejected"}.get(L["kind"], "not_duplicate"),
                           "latent_engagement": round(L["latent"], 6), "planned_path": L.get("path"), "first_contact_breach_planned": L["fc_breach"],
                           "injected_process_issues": ";".join(sorted(set(L["issues"]))) or None,
                           "expected_final_status": L["status"], "expected_conversion": L["status"] == "CONVERTED",
                           "expected_specialist_need": L["spec_need"], "reopened": bool(L.get("reopened"))}


def normalize(name):
    s = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    s = re.sub(r"\b(ltd|inc|corp|limited|incorporated|holdings)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


Engine.finish = _finish


# ============================================================ writing
TYPE = {"varchar": pa.string(), "boolean": pa.bool_(), "integer": pa.int64(), "date": pa.date32(), "timestamp": pa.timestamp("us")}
TABLE_OF = {"prospect": "crm_business_prospect", "lead": "crm_lead", "touch": "crm_lead_source_touch", "channel": "crm_acquisition_channel",
            "campaign": "crm_campaign", "rm": "crm_relationship_manager", "territory": "crm_territory", "rm_territory": "crm_rm_territory",
            "rm_spec": "crm_rm_specialization", "snapshot": "crm_rm_capacity_snapshot", "decision": "crm_routing_decision",
            "assignment": "crm_lead_assignment", "activity": "crm_lead_activity", "status": "crm_lead_status_history", "sla": "crm_sla_event",
            "dup": "crm_duplicate_group", "opp": "crm_opportunity_outcome", "rule_version": "crm_rule_version"}
EVENT_COL = {"crm_lead": "captured_at", "crm_lead_source_touch": "touched_at", "crm_rm_capacity_snapshot": "snapshot_as_of_utc",
             "crm_routing_decision": "decided_at", "crm_lead_assignment": "assigned_at", "crm_lead_activity": "activity_at",
             "crm_lead_status_history": "changed_at", "crm_sla_event": "started_at", "crm_duplicate_group": "flagged_at",
             "crm_opportunity_outcome": "opened_at", "crm_business_prospect": "created_at", "crm_campaign": "created_at",
             "crm_relationship_manager": "created_at", "crm_territory": "created_at", "crm_rm_territory": "created_at",
             "crm_rm_specialization": "created_at", "crm_acquisition_channel": "created_at", "crm_rule_version": "effective_from"}


def arrow_type(t):
    m = re.match(r"decimal\((\d+),(\d+)\)", t)
    return pa.decimal128(int(m.group(1)), int(m.group(2))) if m else TYPE[t]


def to_value(v, t):
    if v is None:
        return None
    if t == "timestamp":
        return dt.datetime.fromtimestamp(v, UTC).replace(tzinfo=None)
    if t.startswith("decimal"):
        scale = int(re.match(r"decimal\(\d+,(\d+)\)", t).group(1))
        return Decimal(str(v)).quantize(Decimal(1).scaleb(-scale))
    return v


def write_parquet(path, rows, cols):
    arrays, fields = [], []
    for name, t, nullable in cols:
        typ = arrow_type(t)
        arrays.append(pa.array([to_value(r[name], t) for r in rows], type=typ))
        fields.append(pa.field(name, typ, nullable=nullable))
    table = pa.Table.from_arrays(arrays, schema=pa.schema(fields))
    pq.write_table(table, path, compression="zstd", compression_level=3, row_group_size=1_000_000, use_dictionary=True,
                   write_statistics=True, store_schema=True)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write_outputs(E, out, truth_out):
    cfg = E.cfg
    out.mkdir(parents=True, exist_ok=True)
    truth_out.mkdir(parents=True, exist_ok=True)
    (out / "reference").mkdir(exist_ok=True)
    cols_by_table = {}
    for f in cfg.fields:
        cols_by_table.setdefault(f["table_name"], []).append((f["column_name"], f["data_type"], f["nullable"] == "true"))
    sort_keys = {"touch": ("touched_at", "source_touch_id"), "rm_territory": ("rm_territory_id",), "rm_spec": ("rm_specialization_id",)}
    manifest = []
    for key, table in TABLE_OF.items():
        rows = E.T[key]
        cols = cols_by_table[table]
        names = [c[0] for c in cols]
        for r in rows:
            missing = [n for n in names if n not in r]
            if missing:
                raise SystemExit(f"{table}: generator row lacks {missing}")
        pk = E.cfg.entities[table]["primary_key"]
        rows = sorted(rows, key=lambda r: r[pk])
        path = out / f"{table}.parquet"
        write_parquet(path, rows, cols)
        ev = EVENT_COL[table]
        vals = [r[ev] for r in rows if r.get(ev) is not None]
        conv = (lambda v: dt.datetime.fromtimestamp(v, UTC).date().isoformat()) if isinstance(vals[0], int) else (lambda v: str(v))
        manifest.append({"entity_name": table, "file_name": path.name, "grain": E.cfg.entities[table]["grain"], "primary_key": pk,
                         "row_count": len(rows), "event_date_column": ev, "min_event_date": conv(min(vals)), "max_event_date": conv(max(vals)),
                         "sha256": sha(path), "generator_version": cfg.p["generator_version"], "master_random_seed": cfg.seed,
                         "schema_version": cfg.p["schema_version"], "file_role": "entity"})
    # reference lookup
    ref = out / "reference" / "synthetic_fsa_lookup.csv"
    with open(ref, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["postal_fsa", "province_code", "cma_code_sgc2021", "resolution", "lookup_version", "note"])
        for r in E.fsa_rows:
            w.writerow([r["postal_fsa"], r["province_code"], r["cma_code_sgc2021"] or "", r["resolution"], "FSA-SYN-1",
                        "synthetic lookup; not an official postal conversion"])
    manifest.append({"entity_name": "synthetic_fsa_lookup", "file_name": "reference/synthetic_fsa_lookup.csv", "grain": "one row per synthetic FSA",
                     "primary_key": "postal_fsa", "row_count": len(E.fsa_rows), "event_date_column": "", "min_event_date": "", "max_event_date": "",
                     "sha256": sha(ref), "generator_version": cfg.p["generator_version"], "master_random_seed": cfg.seed,
                     "schema_version": cfg.p["schema_version"], "file_role": "reference"})
    with open(out / "synthetic_crm_manifest.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(manifest[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(manifest)
    # ground truth (validation only)
    tman = []
    truth_tables = {
        "lead_truth": (sorted(E.truth.values(), key=lambda r: r["lead_id"]),
                       [("lead_id", "varchar", False), ("primary_scenario_id", "varchar", True), ("arrival_kind", "varchar", False),
                        ("matched_lead_id", "varchar", True), ("expected_duplicate_outcome", "varchar", False), ("latent_engagement", "double", False),
                        ("planned_path", "varchar", True), ("first_contact_breach_planned", "boolean", False), ("injected_process_issues", "varchar", True),
                        ("expected_final_status", "varchar", False), ("expected_conversion", "boolean", False),
                        ("expected_specialist_need", "varchar", True), ("reopened", "boolean", False)]),
        "lead_scenario_membership": (sorted(E.membership, key=lambda r: (r["lead_id"], r["scenario_id"])),
                                     [("lead_id", "varchar", False), ("scenario_id", "varchar", False)]),
        "prospect_truth": (sorted(E.prospect_truth + [{"prospect_id": P["prospect_id"], "relationship": "duplicate_record_of_same_business"
                                                       if P.get("dup_rule") and any(E.leads[l]["status"] == "MERGED" for l in P["leads"]) else "different_business_similar_name",
                                                       "related_prospect_id": P["twin_of"], "rule_expected": P.get("dup_rule")}
                                                      for P in E.prospects.values() if P.get("twin_of")], key=lambda r: r["prospect_id"]),
                           [("prospect_id", "varchar", False), ("relationship", "varchar", False), ("related_prospect_id", "varchar", True),
                            ("rule_expected", "varchar", True)]),
        "routing_decision_truth": (sorted([{**t, "excluded_hard_limit_rm_ids": ";".join(t["excluded_hard_limit_rm_ids"]) or None} for t in E.decision_truth],
                                          key=lambda r: r["routing_decision_id"]),
                                   [("routing_decision_id", "varchar", False), ("excluded_hard_limit_rm_ids", "varchar", True),
                                    ("boundary_exclusion", "boolean", False), ("preferred_rm_ignored", "boolean", False)]),
    }
    TYPE["double"] = pa.float64()
    for name, (rows, cols) in truth_tables.items():
        p = truth_out / f"{name}.parquet"
        write_parquet(p, rows, cols)
        tman.append({"file_name": p.name, "row_count": len(rows), "sha256": sha(p), "purpose": "generator ground truth for validation only; never a model or routing input",
                     "generator_version": cfg.p["generator_version"], "master_random_seed": cfg.seed})
    with open(truth_out / "ground_truth_manifest.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(tman[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(tman)
    return manifest


def publish(tmp_out, tmp_truth):
    for src, dst in ((tmp_out, PUBLISH), (tmp_truth, PUBLISH_TRUTH)):
        backup = dst.with_name(dst.name + "_previous")
        if backup.exists():
            shutil.rmtree(backup)
        if dst.exists():
            dst.rename(backup)
        shutil.move(str(src), str(dst))
        if backup.exists():
            shutil.rmtree(backup)


def file_hashes(folder):
    return {p.relative_to(folder).as_posix(): sha(p) for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix != ".json"}


def check_reproducibility(cfg):
    rows, runs = [], []
    for k in (1, 2):
        base = ROOT / "target" / f"synthetic_crm_repro_{k}"
        if base.exists():
            shutil.rmtree(base)
        E = build(cfg, base / "crm", base / "crm_ground_truth")
        write_outputs(E, base / "crm", base / "crm_ground_truth")
        runs.append({**{f"crm/{p}": h for p, h in file_hashes(base / "crm").items()},
                     **{f"crm_ground_truth/{p}": h for p, h in file_hashes(base / "crm_ground_truth").items()}})
    pub = {**{f"crm/{p}": h for p, h in file_hashes(PUBLISH).items()}, **{f"crm_ground_truth/{p}": h for p, h in file_hashes(PUBLISH_TRUTH).items()}}         if PUBLISH.exists() else {}
    for f in sorted(set(runs[0]) | set(runs[1]) | set(pub)):
        a, b, c = runs[0].get(f), runs[1].get(f), pub.get(f)
        rows.append({"file": f, "run_1_sha256": a, "run_2_sha256": b, "published_sha256": c, "identical": a is not None and a == b == c})
    out = ROOT / "data" / "validation" / "synthetic_crm"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "reproducibility_check.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    for k in (1, 2):
        shutil.rmtree(ROOT / "target" / f"synthetic_crm_repro_{k}", ignore_errors=True)
    ok = all(r["identical"] for r in rows)
    print(f"Two-run reproducibility: {sum(r['identical'] for r in rows)} of {len(rows)} business-data files identical in both runs and the published data "
          "(only synthetic_crm_run.json, the run-timestamp file, is excluded)")
    print("REPRODUCIBILITY " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-root", help="write here instead of a temporary folder under target/ (no publishing)")
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--check-reproducibility", action="store_true",
                    help="generate twice into scratch folders and compare every business-data file with each other and with the published data")
    args = ap.parse_args()
    cfg = Config()
    if args.check_reproducibility:
        sys.exit(check_reproducibility(cfg))
    base = Path(args.output_root) if args.output_root else ROOT / "target" / "synthetic_crm_tmp"
    if base.exists():
        shutil.rmtree(base)
    out, truth = base / "crm", base / "crm_ground_truth"
    started = dt.datetime.now(UTC).replace(microsecond=0).isoformat()
    E = build(cfg, out, truth)
    manifest = write_outputs(E, out, truth)
    for m in manifest:
        print(f"  {m['entity_name']:<28} {m['row_count']:>7} rows  {m['min_event_date']} .. {m['max_event_date']}")
    if not args.no_validate:
        r = subprocess.run([sys.executable, str(VALIDATOR), "--source-only", "--data-root", str(base)])
        if r.returncode != 0:
            print("SYNTHETIC CRM GENERATION FAILED: validation of the temporary output failed; nothing published")
            sys.exit(1)
    (out / "synthetic_crm_run.json").write_text(json.dumps({"run_timestamp_utc": started, "generator_version": cfg.p["generator_version"],
                                                            "master_random_seed": cfg.seed, "note": "run metadata only; not part of any hash"},
                                                           indent=2) + "\n", encoding="utf-8", newline="\n")
    if args.output_root or args.no_publish:
        print(f"Generated into {base} (not published)")
        return
    publish(out, truth)
    shutil.rmtree(base, ignore_errors=True)
    print("SYNTHETIC CRM DATA PUBLISHED to data/synthetic/crm and data/synthetic/crm_ground_truth")


if __name__ == "__main__":
    main()
