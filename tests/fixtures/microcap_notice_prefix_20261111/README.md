# Original D11 notice proof bytes

These two public source proofs were copied without rewriting from the existing
P2 source-preparation cache. Their original paths, dates, observations, authority
references and raw bytes are retained.

| File | Original generation | Raw SHA-256 |
| --- | --- | --- |
| `advance-proof.json` | `refresh_20261111T143208796316Z_2e7092817919/proof.json` | `1afcedebbb8e0e2adbe1a529ffbb7b1d3728ec15fe91c0f8550c3daa1bfe672c` |
| `self-proof.json` | `refresh_20261112T010443196822Z_1489267b2428/proof.json` | `651db4226dbdb200222be74017b7ca332adb838bd57bec213f18670a809a256b` |

The advancing receipt moves D10/62a to D11/db23. The later receipt rechecks D11
without changing that authority. They are small predecessor-discovery fixtures,
not their full archived source closure and not D12 evidence.

The adversarial test imports the exact unchanged M7 `original_notice_chain`
function in a fresh process. It adds an explicitly software D12 leaf, reproduces
the older-proof lookup failure, and stops at a sentinel at the downstream
`verify_notice_chain` gate after the predecessor choice is corrected. It does
**not** claim that the D12 Native data or formal delivery gate passed.
