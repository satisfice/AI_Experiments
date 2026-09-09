# AI Output Format

This project measures how prompt phrasing and output-format strictness affect the structural consistency of list-style outputs across LLMs, temperatures, and repeated runs.

## Language

### Running trials

**Experiment**:
A named subject area for a batch of Trials (e.g. `animals`, `addresses`). The second segment of a Trial's filename.
_Avoid_: subject, topic, batch

**Prompt**:
A named instruction template sent to a model, asking it to produce a list of items. Stored as a `.prompt` file (e.g. `animals_hard.prompt`).
_Avoid_: instruction, template, question

**Trial**:
A single result file: one model's response to one Experiment/Prompt/Format/Format Hardness/Temperature combination, at one Iteration.
_Avoid_: run, result, response, sample

**Iteration**:
The repetition number of a Trial run against the same TrialKey, used to measure output variance across repeated runs of the same combination.
_Avoid_: repeat, run number

**TrialKey**:
The identity shared by every Trial in a TrialSet: (model, temperature, format, format hardness, prompt).
_Avoid_: combo, lookup key

**TrialSet**:
Every Trial that shares one TrialKey — i.e. all Iterations of the same model/temperature/format/format-hardness/prompt combination.
_Avoid_: group, batch, run group

### Format vocabulary

**Format**:
The output structure requested from the model: text, numberedText, JSON, YAML, markdown, HTML, or CSV.
_Avoid_: file type, extension

**Format Hardness**:
How prescriptive the formatting instruction appended to a Prompt is. `soft` asks loosely ("Return results in JSON format"); `hard` dictates exact casing, punctuation, and delimiter rules.
_Avoid_: strictness, format level

**Format Style**:
The structural style actually detected in a Trial's output, independent of what was requested (e.g. JSON rendered as "single lines" vs. "multiple lines"; YAML as "leading hyphen" vs. "plain text").
_Avoid_: detected format

**Consistent Format**:
Whether every Trial in a TrialSet was rendered in the same Format Style.
_Avoid_: uniform format

### Quality vocabulary

**Item**:
One parsed unit of a Trial's output — a single list entry (e.g. one animal name).
_Avoid_: entry, result, line

**Cleanup Rule**:
An automatic, silent normalization applied to an Item during parsing (e.g. stripping a markdown header line or bold/italic markers).
_Avoid_: transform, fix

**Quality Issue**:
A defect detected in an Item that is flagged rather than silently corrected (e.g. leading/trailing punctuation, exceeding max length, a Preamble Leak, a markup artifact, repeated characters).
_Avoid_: problem, error, defect

**Preamble Leak**:
A Quality Issue where a Trial's output includes conversational or instructional filler text instead of a real Item.
_Avoid_: leak, noise

**Case**:
Whether an Item's text is lowercased — the expected convention — or not; a per-TrialSet consistency dimension like Format Style.
_Avoid_: casing

**Codeblock**:
Whether a Trial's output was wrapped in a markdown code fence; tracked as a consistency dimension across a TrialSet.
_Avoid_: fence, code fence
