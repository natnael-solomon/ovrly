# PDP 1321/2024: Articles 18-22 operational checklist

This is **not legal advice**, certification or a complete compliance checklist.
The demo uses consented or synthetic material only. Consent alone must not be
treated as resolving every transfer/localization obligation.

Sources checked 4 October 2026:
[Ministry of Justice publication page](https://justice.gov.et/en/law/personal-data-protection-proclamation/)
and the English text of Federal Negarit Gazette No. 35, 24 July 2024,
pages 15644-15647, in this
[gazette copy](https://www.dataguidance.com/sites/default/files/personal_data_protection_proclamation_1321-2024.pdf).
The Ministry page links the gazette PDF. Recheck applicable Authority
directions and obtain qualified local advice before real-data deployment.

Articles 18-22 address **transfers and sovereignty**, not a five-article
access/erasure checklist. Retention tooling is evidence for data minimization
and deletion operations; it does not establish permission to transfer data.

| Article | Operational question / required evidence | Current status |
| --- | --- | --- |
| 18: principle of data transfer | Inventory every destination that processes personal data; establish appropriate protection and the applicable transfer conditions before transmission. | Open: no production deployment/provider transfer authorized by this work. On-device screen-text recognition sends nothing to Google: frames stay on the phone and ML Kit's telemetry upload components are removed (decision [0005](../decisions/0005-on-device-screen-text.md); verified on a device: no Google logging traffic from the app). |
| 19: level of protection in third-party jurisdiction | Assess the data's nature, purpose and processing duration, origin/destination, relevant law, professional rules and safeguards. Record any required Authority determination, restrictions and ongoing review. | Open: owner/legal review and provider/hosting evidence required. |
| 20: conditions for cross-border transfer | Document the applicable condition for each transfer, including any required Authority proof/determination, explicit informed consent, necessity or eligible public-register basis. Do not assume public availability permits unrestricted onward transfer. | Open: no legal basis is inferred from a provider API key or a free account. |
| 21: safeguards before cross-border transfer | Retain evidence of effective safeguards and relevant legitimate interests for an Authority request; be able to stop/suspend transfers or satisfy imposed conditions. | Partial technical evidence: content-free logs and local retention tests. Actual transfer controls and Authority-facing evidence remain open. |
| 22: data sovereignty | Verify storage in Ethiopia for locally collected/obtained personal data; assess any critical-data processing requirements and prior Authority approval for cross-border sensitive-data transfers. Record actual data-center locations, not company headquarters. | Open: overseas free-tier hosting must not be declared compliant without addressing these requirements. |

## Owner release gates

- [ ] Approve the [data map](data-map.md), controller/processor responsibilities
  and a versioned destination/subprocessor inventory, including backups and logs.
- [ ] Obtain hosting region, retention/deletion and onward-transfer evidence;
  resolve Article 22 before selecting a deployment merely on price.
- [ ] Record informed consent and appropriate treatment of incidental third-party
  material. Use wholly synthetic content when permission is uncertain.
- [ ] Have qualified reviewers assess Articles 18-22 and applicable Authority
  requirements; record determinations/approvals where required.
- [ ] Accept or revise [BC-D06](../decisions/BC-D06-retention.md); configure
  host logs/backups and verify storage deletion and isolated restore cleanup.
- [ ] Exercise a real transfer-stop procedure before adding a provider; the
  global intake-stop implementation is still outside #77.

All gates above remain unchecked until evidence is supplied. No legal approval,
live-provider deletion, backup erasure or production readiness is claimed.
