# Investigation eval 20261014T091001Z

Cases: 27 · models: haiku, sonnet, nova-lite · judge: global.anthropic.claude-haiku-5-5 · spent: $1.3492

| Model | Mean score | All checks passed | Unsupported claims | Errors | Mean cost | Mean seconds |
|---|---|---|---|---|---|---|
| haiku | 0.936 | 19/27 | 6 | 0 | $0.0026 | 17.4 |
| sonnet | 0.964 | 22/27 | 4 | 0 | $0.0464 | 17.8 |
| nova-lite | 0.905 | 15/27 | 6 | 0 | $0.0010 | 10.7 |

## Failed checks by case

- tg_real / nova-lite: mentions
- tg_who / nova-lite: claims_supported
- tg_unauth_why / nova-lite: mentions
- sr_path / nova-lite: mentions
- sr_dwell_len / nova-lite: claims_supported
- sr_dwell_doing / haiku: claims_supported
- sr_dwell_doing / nova-lite: hedges
- sr_nobadge / nova-lite: mentions
- it_real / nova-lite: hedges
- it_label / haiku: hedges
- it_label / nova-lite: hedges
- ah_breach / sonnet: claims_supported
- bwe_entered / haiku: claims_supported
- bwe_stolen / sonnet: claims_supported
- e1_letin / haiku: claims_supported
- e1_letin / nova-lite: claims_supported, mentions
- e1_whose / sonnet: claims_supported
- e2_holder / nova-lite: claims_supported, hedges
- e2_first / haiku: no_forbidden_claim
- e3_allowed / sonnet: claims_supported
- e3_who / haiku: claims_supported
- e6_escalate / haiku: claims_supported, mentions
- e6_escalate / sonnet: mentions
- e6_escalate / nova-lite: mentions
- e6_doing / haiku: claims_supported
