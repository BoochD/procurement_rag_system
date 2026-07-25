# Legal Security, National Regime, Participant Requirements And Penalty Checks

This document plans the next legal/rule-check layer for:

- bid security;
- contract performance security;
- warranty-obligation security;
- national regime rows under PP No. 1875;
- additional participant requirements under Article 31 part 2.1 of Law No. 44-FZ;
- contract penalties/fines under PP No. 1042.

It is based on the user checklist, local document examples, and current public
legal text found on 2026-07-24. It is a development plan, not legal advice.

## Important Separation

These checks must not be merged into one “штрафы” check.

- Обеспечения are procurement/contract security conditions.
- PP No. 1875 rows are national-regime conditions in the plan.
- Additional requirements under Article 31 part 2.1 are participant eligibility
  conditions.
- Штрафы / пени are responsibility clauses in the draft contract.

They share the same inputs, mostly NMCK/contract price and procurement method,
but they are different legal checks and should be reported separately.

## Where These Clauses Appear In Local Documents

The full scan output is stored in:

`runtime/legal_clause_locations.txt`

Observed patterns:

### Schedule Application / Plan-Graph

The plan is the main source for these fields.

Examples:

- `doci_primery/PACK_06_05/1_Заявка_на_включение_в_план_график.docx`
  - `Размер обеспечения заявки`: `0 %`
  - `Размер обеспечения исполнения контракта`: `0 %`
  - `Размер обеспечения гарантийных обязательств`: `0 %`
  - `Применение национального режима ... № 1875`
  - `17.1. Запреты`
  - `17.2. Ограничения`
  - `17.3. Преимущества`
  - `Дополнительные требования к участникам закупки ... ч. 2.1 ст. 31`

- `doci_primery/Cartridges/1. Заявка в ПГ(2).docx`
  - `Размер обеспечения заявки`: `1%`
  - `Размер обеспечения исполнения контракта`: `10%`
  - `Размер обеспечения гарантийных обязательств`: `10%`
  - national-regime rows are filled per OKPD2.

The plan parser should treat these rows as deterministic key-value rows.

### Draft Contract

The contract is the main source for:

- actual contract-security clause;
- warranty-security clause;
- penalty/fine clauses;
- SMP/SONKO subcontracting penalty clause, if subcontracting is required.

Observed examples:

- `doci_primery/PACK_06_05/4_Проект_контракта_шины_и_комплектующие.docx`
  - section around responsibility clauses contains:
    - supplier/customer right to demand `неустоек (штрафов, пеней)`;
    - PP No. 1042 reference;
    - customer fine: `1000 рублей`;
    - supplier fine: `10 процентов от цены контракта`;
    - non-value obligation fine: `1000 рублей`;
    - penalty formula under Article 34.

- `doci_primery/Cartridges/4. Проект контракта_картриджи.docx`
  - section VI has responsibility clauses;
  - section VIII usually contains security conditions;
  - warranty obligations can be referenced in acceptance clauses.

### OOZ

OOZ usually contains warranty requirements, delivery/place terms, and sometimes
additional-characteristic justifications. It is not the primary source for
security sizes or penalty tiers.

### ONMCK

ONMCK supplies NMCK and sometimes stage prices. It does not normally contain
security sizes or contract penalty clauses.

## Legal Sources To Encode

### Bid Security: Law No. 44-FZ Article 44

Source:

- ConsultantPlus, Article 44:
  `https://www.consultant.ru/document/cons_doc_LAW_144624/f0d585697b9aa54ef56a166f7c33e3f0e609889e/`

Rules for first implementation:

- For competitive procedures, customer must set bid security.
- If NMCK is not more than 1,000,000 RUB, customer may not set bid security.
- If NMCK is not more than 20,000,000 RUB, bid security is from 0.5% to 1% NMCK.
- If NMCK is more than 20,000,000 RUB, bid security is from 0.5% to 5% NMCK.
- Some participant categories have special treatment; do not implement those
  until the package gives enough participant context.

Implementation note:

- For single-supplier purchases without competitive procedure, this check should
  usually be `not_applicable` or `manual_review`, not a failed check.

### Contract Performance Security: Law No. 44-FZ Article 96

Source:

- ConsultantPlus, Article 96:
  `https://www.consultant.ru/document/cons_doc_LAW_144624/de5cd3096c9ee62e2f4e4a63009e6c00e845e0fc/`

Rules for first implementation:

- Article 96 generally requires contract performance security, except cases in
  part 2 and other exceptions.
- For many single-supplier cases under Article 93, customer may set security
  rather than must set it.
- If security is set, general range is 0.5% to 30% NMCK.
- If there is an advance, security must be not less than the advance, with
  special rules for advances over 30% and treasury support.
- If procurement is under Article 30 part 1, security can be calculated from
  contract price rather than NMCK under Article 96 part 6.2.

Implementation note:

- First version should check:
  - value exists in plan;
  - value is also found or explicitly absent in contract;
  - if set, percent/amount is within simple range;
  - if `не предусмотрено` is found in contract and plan says `0%`, pass.
- Advance, treasury support and lifecycle-contract branches should be reported
  as manual review unless fields are confidently extracted.

### Warranty-Obligation Security: Law No. 44-FZ Article 96 Part 2.2

Source:

- ConsultantPlus, Article 96, part 2.2:
  `https://www.consultant.ru/document/cons_doc_LAW_144624/de5cd3096c9ee62e2f4e4a63009e6c00e845e0fc/`

Rules for first implementation:

- Customer may set warranty-obligation security only if warranty obligations are
  established under Article 33 part 4.
- Size cannot exceed 10% of NMCK, or contract price for single-supplier contract.

Implementation note:

- If plan says `0%` and contract says security is not required, pass.
- If OOZ/contract contains warranty requirements and plan sets warranty security,
  check it is <= 10%.
- If warranty requirements exist but security is absent, do not fail by default;
  Article 96 part 2.2 says customer may establish it. Report as found/absent.

### Additional Participant Requirements: Article 31 Part 2.1

Source:

- ConsultantPlus, Article 31:
  `https://www.consultant.ru/document/cons_doc_LAW_144624/be7f337d9b35705ac035531878c8d15c2b09b36d/`

Rules for first implementation:

- Applies to competitive procedures.
- If NMCK is 20,000,000 RUB or more, and no special additional requirements
  under Article 31 part 2 apply, customer establishes experience requirement.
- Participant must have an executed contract or 223-FZ agreement within three
  years before application date.
- Value of performed obligations must be at least 20% of NMCK.
- If penalties/fines were charged under that previous contract/agreement, they
  must be paid.

Implementation note:

- This is not a penalty-size check in the current procurement contract.
- First version checks only that the plan row is filled consistently:
  - NMCK < 20m: `нет`, `отсутствуют`, or empty acceptable with warning style
    depending on current report policy.
  - NMCK >= 20m and competitive: row should not be empty; if says absent,
    report manual review unless special Article 31 part 2 / PP 2571 branch is
    extracted.
- Full validation needs PP No. 2571 and procurement object category; defer.

### National Regime: PP No. 1875

Source:

- ConsultantPlus, PP No. 1875:
  `https://www.consultant.ru/document/cons_doc_LAW_494318/92d969e26a4326c5d02fa79b8f9cf4994ee5633b/`

Rules for first implementation:

- Use existing local PP1875 artifacts by OKPD2.
- For each OKPD2 matched in Appendix 1 or 2, check that the plan national-regime
  rows are filled:
  - `17.1 Запреты`;
  - `17.2 Ограничения`;
  - `17.3 Преимущества`.
- The report should show:
  - OKPD2;
  - matched parent code;
  - appendix/table/position;
  - reference name;
  - plan row content;
  - whether the row is filled and plausible.

Implementation note:

- This remains local registry logic, not live external validation.
- Do not require an exact legal phrase in the plan. Check for presence of the
  relevant code/position or a clear non-application reason.

### Penalties And Fines: PP No. 1042

Sources:

- ConsultantPlus, PP No. 1042:
  `https://www.consultant.ru/document/cons_doc_LAW_227100/`
- LegalActs full text:
  `https://legalacts.ru/doc/postanovlenie-pravitelstva-rf-ot-30082017-n-1042-ob-utverzhdenii/`
- ConsultantPlus calculator page:
  `https://calc.consultant.ru/44z`

Rules for first implementation:

1. Supplier fine for value-bearing obligations, PP No. 1042 paragraph 3:

   - contract/stage price <= 3m: 10%;
   - 3m..50m: 5%;
   - 50m..100m: 1%;
   - 100m..500m: 0.5%;
   - 500m..1b: 0.4%;
   - 1b..2b: 0.3%;
   - 2b..5b: 0.25%;
   - 5b..10b: 0.2%;
   - >10b: 0.1%.

2. Supplier fine for Article 30 part 1 SMP/SONKO procurement, PP No. 1042
   paragraph 4:

   - 1% of contract/stage price;
   - not more than 5,000 RUB;
   - not less than 1,000 RUB.

3. Supplier fine for non-value obligations, PP No. 1042 paragraph 6:

   - price <= 3m: 1,000 RUB;
   - 3m..50m: 5,000 RUB;
   - 50m..100m: 10,000 RUB;
   - >100m: 100,000 RUB.

4. Fine for failure to attract SMP/SONKO subcontractors, PP No. 1042 paragraph 8:

   - 5% of the subcontracting volume established by contract.

5. Customer fine, PP No. 1042 paragraph 9:

   - price <= 3m: 1,000 RUB;
   - 3m..50m: 5,000 RUB;
   - 50m..100m: 10,000 RUB;
   - >100m: 100,000 RUB.

6. If contract has stages, PP No. 1042 paragraph 2 allows calculating from
   stage price.

7. Total supplier/customer fines cannot exceed contract price under paragraphs
   11 and 12.

Implementation note:

- Peni are formulas, not fixed fines. First version checks presence of formulas
  from Article 34, not exact runtime amount.
- Exact penalty sums are only checked for fixed fine clauses.

## Extraction Fields Needed

Existing fields:

- `schedule_application.application_security`
- `schedule_application.contract_security`
- `schedule_application.warranty_security`
- `schedule_application.national_regime_raw`
- `schedule_application.national_regime_fields`
- `schedule_application.additional_participant_requirements_text`
- `contract_draft.contract_security`
- `contract_draft.warranty_security`
- `contract_draft.subcontract_smp_sonko_required`
- `contract_draft.subcontract_smp_sonko_percent`

Suggested additions:

```python
class PenaltyClause(BaseModel):
    party: Literal["supplier", "customer", "unknown"]
    obligation_kind: Literal[
        "value_obligation",
        "non_value_obligation",
        "delay_peni",
        "smp_sonko_subcontract",
        "unknown",
    ]
    raw_text: str
    percent: Decimal | None = None
    amount: Decimal | None = None
    basis: str | None = None
    evidence: str | None = None

class ContractDraftSchema(BaseModel):
    penalty_clauses: list[PenaltyClause] = []
    peni_clauses: list[PenaltyClause] = []
```

Keep it simple: LLM extracts raw clauses; deterministic checks interpret common
percent/amount patterns.

## Check Algorithm

### 1. Determine Context

Input:

- plan NMCK;
- contract price;
- stage prices if present;
- procurement method from plan/request/contract;
- SMP/SONKO preference and subcontracting obligation;
- advance text/amount if extracted;
- warranty requirements;
- PP1875 OKPD matches.

If context is missing, return manual review with the missing field name.

### 2. Bid Security Check

- Read plan `application_security`.
- If procurement method is single supplier: usually not applicable/manual review.
- If competitive and NMCK <= 1m:
  - `0%` or `не предусмотрено` is acceptable.
- If competitive and 1m < NMCK <= 20m:
  - percent must be 0.5..1.
- If competitive and NMCK > 20m:
  - percent must be 0.5..5.
- Report found percent and expected range.

### 3. Contract Security Check

- Read plan `contract_security`.
- Read contract `contract_security`.
- If plan says `0%` and contract says `не предусмотрено`: pass.
- If security is set:
  - check simple 0.5..30% range;
  - if advance exists, mark manual review until advance extraction is reliable.
- Compare plan and contract values.

### 4. Warranty Security Check

- Read plan `warranty_security`.
- Read contract `warranty_security`.
- If value is set, verify <= 10%.
- If absent, do not fail solely because warranty exists; Article 96 part 2.2 is
  optional.
- Compare plan and contract values.

### 5. Additional Participant Requirements Check

- If NMCK < 20m: plan row should be `нет` / `отсутствуют` / equivalent.
- If NMCK >= 20m and procurement is competitive:
  - plan row must be filled;
  - if it says absent, return manual review unless extracted branch explains
    PP2571/article 31 part 2 applies instead.
- For single supplier: usually not applicable/manual review.

### 6. PP1875 Plan Rows Check

- Use local OKPD2->PP1875 matching.
- For each matched OKPD:
  - Appendix 1: expect a filled `Запреты` row or clear non-application reason.
  - Appendix 2: expect a filled `Ограничения` row or clear non-application reason.
  - Advantages row is checked for filled/negative value, not strict formula.
- Report every OKPD with matched position and plan row value.

### 7. Penalty Clause Check

- Extract responsibility section from contract.
- Classify clauses:
  - supplier value-obligation fine;
  - supplier non-value-obligation fine;
  - customer fine;
  - delay peni formula;
  - SMP/SONKO subcontracting fine.
- Calculate expected values from contract price or stage price:
  - if stages exist and clause says stage price, calculate per stage;
  - otherwise use contract price.
- Compare:
  - supplier value percent;
  - supplier non-value fixed amount;
  - customer fixed amount;
  - SMP/SONKO 5% if subcontracting obligation exists;
  - peni formula presence.

Example:

- Contract price: 350,000 RUB.
- Supplier value-obligation fine under PP1042 p.3: 10%.
- Customer fine under PP1042 p.9: 1,000 RUB.
- Supplier non-value-obligation fine under PP1042 p.6: 1,000 RUB.
- If purchase is Article 30 part 1 SMP/SONKO: supplier fine under p.4 would be
  1% but min 1,000 and max 5,000.

## Report Shape

Recommended report sections:

1. Обеспечение заявки:
   - found in plan;
   - expected range;
   - verdict.

2. Обеспечение исполнения контракта:
   - plan value;
   - contract value;
   - expected range;
   - verdict.

3. Обеспечение гарантийных обязательств:
   - plan value;
   - contract value;
   - max 10% check if set;
   - verdict.

4. Национальный режим / ПП №1875:
   - OKPD2;
   - matched appendix;
   - plan row content;
   - verdict.

5. Дополнительные требования к участникам:
   - NMCK;
   - procurement method;
   - plan row content;
   - whether Article 31 part 2.1 threshold applies.

6. Штрафы и пени проекта контракта:
   - supplier value-obligation fine;
   - supplier non-value-obligation fine;
   - customer fine;
   - peni formulas;
   - SMP/SONKO subcontracting fine if needed.

## First Implementation Scope

Do now:

- schema for penalty clauses;
- LLM extraction of responsibility/security/additional-requirement clauses;
- deterministic checks for simple percent/amount ranges;
- report found values and expected values.

Do later:

- full PP2571 branch detection;
- advance/treasury/lifecycle special cases;
- participant-category special bid-security treatment;
- legal expert review of edge cases.

