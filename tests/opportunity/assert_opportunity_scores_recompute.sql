{{ config(tags=['opportunity']) }}
-- Pillar scores recompute from the published normalized components and within-pillar weights, and the final score
-- recomputes from the published pillar scores and pillar weights. Tolerance 1e-9 (floating-point summation only).
-- Pillar and final scores stay within 0-100. Context columns play no part in the recomputation.
select cma_code_sgc2021, scenario_id, opportunity_score
from {{ ref('mart_cma_opportunity_framework') }}
where abs(pillar_scale - c01_normalized) > 1e-9
   or abs(pillar_momentum - (within_momentum_weight_c05 * c05_normalized + within_momentum_weight_c09 * c09_normalized)) > 1e-9
   or abs(pillar_dynamism - c03_normalized) > 1e-9
   or abs(pillar_commercial_fit - c15_normalized) > 1e-9
   or abs(pillar_stability - c10_stability_normalized) > 1e-9
   or abs(opportunity_score - (weight_scale * pillar_scale + weight_momentum * pillar_momentum + weight_dynamism * pillar_dynamism
                               + weight_commercial_fit * pillar_commercial_fit + weight_stability * pillar_stability)) > 1e-9
   or abs(opportunity_score - (contribution_scale + contribution_momentum + contribution_dynamism + contribution_commercial_fit
                               + contribution_stability)) > 1e-9
   or opportunity_score not between 0 and 100
   or least(pillar_scale, pillar_momentum, pillar_dynamism, pillar_commercial_fit, pillar_stability) < 0
   or greatest(pillar_scale, pillar_momentum, pillar_dynamism, pillar_commercial_fit, pillar_stability) > 100
