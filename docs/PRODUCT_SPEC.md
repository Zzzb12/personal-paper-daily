# Personal Paper Daily Product Specification

## Product summary

Personal Paper Daily is a private research-assistant pipeline for discovering new arXiv work that matches a user's Zotero library, concentrating expensive analysis on the most relevant papers, and delivering evidence-bound Chinese readings through Feishu and a responsive static website.

The primary research focus is efficient image and video diffusion inference, especially Diffusion Transformer acceleration, feature/token caching and reuse, prediction, spatial propagation, cluster-based selection, adaptive scheduling, and training-free acceleration.

## User scenario

The user maintains representative papers and feedback in Zotero. Once per day, the system reads approved Zotero collections, retrieves new papers from selected arXiv categories, ranks them against the user's library, deeply analyzes only the highest-ranked PDFs, and produces:

- A concise Feishu daily briefing containing no more than five highlighted papers.
- A complete static reading site for desktop and mobile.
- Persistent personal feedback states: read, favorite, and irrelevant.
- Traceable evidence for every important technical claim.

The user should be able to decide quickly which papers deserve reading without trusting unsupported summaries.

## Inputs

### Zotero interest collections

Include recursively:

- `PaperDaily/00-Seeds/**`
- `PaperDaily/03-Read/**`
- `PaperDaily/04-Favorite/**`

Exclude recursively, with exclusion taking precedence:

- `PaperDaily/99-Exclude/**`

Core seed topics:

- Diffusion Transformer inference acceleration
- Feature caching
- Token caching
- Feature reuse
- Feature prediction
- Taylor expansion prediction
- Cluster-based token selection
- Spatial feature propagation
- Adaptive cache scheduling
- Image and video diffusion acceleration
- Training-free acceleration

### Daily paper sources

Version 1 retrieves arXiv papers from:

- `cs.CV`
- `cs.LG`
- `cs.AI`

bioRxiv and medRxiv remain upstream capabilities but are outside the first personal-paper-daily release.

### Configuration defaults

- `candidate_pool_size = 30`
- `llm_rerank_limit = 15`
- `full_analysis_limit = 5`
- Maximum three key evidence visuals per paper.
- Maximum five highlighted papers in the detailed Feishu message.
- Lower-priority papers appear on the website without detailed Feishu coverage.

Credentials are environment-only inputs. No secret or private Zotero payload belongs in Git.

## Recommendation flow

1. Resolve Zotero collection paths and fetch eligible paper metadata.
2. Apply include paths, then the higher-priority exclusion path.
3. Build a versioned interest corpus from titles and abstracts without persisting private raw Zotero data in Git.
4. Retrieve the latest papers from the configured arXiv categories.
5. Normalize and deduplicate arXiv versions by stable arXiv ID.
6. Create a cheap candidate pool capped at 30.
7. Rank candidates with embeddings against the eligible Zotero corpus.
8. Optionally apply LLM reranking to at most 15 papers using cached, bounded requests.
9. Select at most five papers for full PDF download, parsing, evidence extraction, and Chinese analysis.
10. Send at most five detailed items to Feishu and publish all retained candidates to the static site.

The ranking record must preserve component scores, model/config versions, ranking timestamp, and the reason a paper was selected or omitted from deep analysis.

## PDF analysis flow

Full PDF work occurs only after ranking.

1. Download the selected PDF with timeout, bounded retry, content-type/size validation, and an idempotent cache key.
2. Preserve original PDF page order and stable page identifiers.
3. Extract page-aware text, document blocks, captions, figures, and tables.
4. Map parser blocks to logical sections and original PDF pages.
5. Prefer evidence from Introduction, Motivation, Observation, Analysis, Method, Experiments, Ablation Study, and Appendix.
6. Generate candidate Insights only from full-document evidence, never from the Abstract alone.
7. Bind every key Insight and ablation statement to evidence.
8. Validate labels, captions, page references, parameter claims, and numerical results before rendering.
9. Return `null` or “论文未明确提供” when evidence cannot be located.

## Chinese analysis contract

Each deeply analyzed paper contains:

- English original title.
- Chinese title.
- Recommendation reason.
- Research problem.
- Core Insight.
- Logic by which the Insight is formed.
- Figure or Table supporting the Insight.
- Explanation of how that Figure/Table supports the Insight.
- Overall Method flow.
- Role of each Method module.
- Differences from prior work.
- Key parameter name, symbol, purpose, and final value.
- Whether the parameter is fixed, empirically chosen, or search-derived.
- Whether the parameter requires separate tuning for different models.
- Parameter-related ablation evidence.
- Main experimental conclusions.
- Limitations explicitly stated by the authors.
- PDF, arXiv, and code links.

The fixed narrative order is:

`Insight → supporting Figure/Table → Method → key parameters → parameter ablations → experimental conclusions → limitations`

User-facing prose is Chinese, but titles, mathematical symbols, model/dataset/metric names, and URLs retain their canonical form where translation could reduce precision.

## Evidence mechanism

### Required evidence fields

Each evidence record stores:

- Stable evidence ID and paper ID.
- PDF page.
- Parser page index when it differs from display page.
- Section title/path.
- Figure/Table label or `null` for text-only evidence.
- Caption or `null`.
- Evidence text.
- Extracted image path or `null`.
- Confidence.
- Claim source: author statement, system summary, or system inference.
- `inferred` boolean.
- Supported claim IDs.
- Explanation of how the evidence supports each claim.

### Evidence rules

1. Insight cannot be inferred only from the Abstract.
2. Every key Insight has at least one resolvable evidence reference.
3. Every ablation conclusion binds to the corresponding Figure or Table.
4. A visual reference is invalid if its label, page, caption, or image cannot be resolved.
5. Never invent parameters, values, figures, tables, settings, results, or links.
6. Author statements, system summaries, and system inferences are visibly distinguished.
7. Every system inference sets `inferred=true`.
8. Missing facts use `null` internally and “论文未明确提供” in Chinese rendering.
9. Up to three highest-value evidence visuals are retained per paper for the first release.
10. Confidence does not override a failed reference check.

## Feishu output

The daily Feishu message is concise, readable on mobile, and limited to five highlighted papers. It contains:

- Date and high-level count.
- For each highlighted paper: Chinese/English title, compact recommendation reason, one core Insight, the strongest evidence pointer, key result, and PDF/arXiv/code links.
- A link to the full static reading page.
- Clear indication when analysis is partial or evidence is unavailable.

The Feishu renderer produces a deterministic payload separate from the authenticated Feishu client. Sending is idempotent per daily digest and has bounded retry. Lower-priority papers do not receive detailed Feishu cards.

## Static web output

The static reader supports desktop and mobile layouts. Every retained paper has searchable metadata, ranking context, and an explicit analysis status; only the papers selected for full analysis (at most five per daily run in version one) contain the complete structured Chinese analysis. It provides:

- Date and relevance filtering.
- Keyword search over titles, authors, categories, summaries, methods, and evidence text.
- Paper cards and full detail views.
- Inline Figure/Table evidence with captions, page/section references, confidence, source type, and support explanation.
- PDF, arXiv, and code links using safe external-link behavior.
- Read, favorite, and irrelevant controls.
- Graceful display of missing evidence and partial analysis.

Generated site data is versioned and validated. Local/private feedback state is not committed by default.

## Feedback state

Each paper may independently carry:

- `read`: the user has read or reviewed it.
- `favorite`: the paper is valuable and should reinforce future interests.
- `irrelevant`: the paper should be down-weighted or excluded from similar recommendations.

Feedback operations are idempotent and keyed by stable paper ID. Conflicting states follow explicit policy: `irrelevant` removes `favorite`; a later explicit favorite removes `irrelevant`; `read` can coexist with either. Feedback must not leak into public static artifacts unless the user explicitly chooses publication.

## Data and privacy

- Zotero keys and LLM/Feishu credentials are read from environment variables or GitHub Secrets.
- Raw Zotero private data, downloaded PDFs, parser caches, LLM caches, logs, and feedback state are ignored by Git.
- Persisted derived records use strict versioned schemas.
- Expensive LLM output is cached by input hash, prompt version, model, provider settings, and schema version.
- The system logs identifiers and statuses, not secret values or unnecessary private text.

## Automation

The final scheduled workflow runs daily in GitHub Actions, supports manual dispatch, separates retrieval/ranking from expensive analysis, preserves cache artifacts safely, and isolates per-paper failures. It records why a run was empty, partial, failed, or successful. Secrets remain in GitHub Secrets and no workflow prints generated configuration containing secret values.

## Non-goals

- Stage 0 does not implement any product module.
- Version 1 is not a general-purpose literature review platform.
- Version 1 does not train a recommendation model.
- Version 1 does not analyze every daily candidate PDF.
- Version 1 does not guarantee extraction from scanned or malformed PDFs.
- Version 1 does not infer unsupported experimental settings.
- Version 1 does not provide collaborative accounts, comments, or a server-side multi-user database.
- Version 1 does not publicly expose private Zotero content or local feedback.
- Version 1 does not require bioRxiv or medRxiv ingestion.

## First-release limits

- A maximum of 30 candidates enters ranking.
- A maximum of 15 enters LLM reranking.
- A maximum of five receives full analysis.
- A maximum of three evidence visuals is retained per deeply analyzed paper.
- A maximum of five papers receives detailed Feishu coverage.
- Low-priority retained papers appear only on the website.
- Missing information remains explicit rather than synthesized.
- Reliability, evidence integrity, and cost control take precedence over daily paper count.

## Stage 0 acceptance boundary

Stage 0 is limited to repository setup, upstream/Hermes inspection, baseline execution, security rules, and documentation. No Zotero request, paper retrieval, PDF download, LLM call, Feishu delivery, viewer build, feedback mutation, or new scheduled business workflow is implemented in this stage.
