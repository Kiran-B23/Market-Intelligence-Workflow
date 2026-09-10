# Learned reviewer feedback
<!-- Appended by `main.py triage`. Read into the recommendation prompt.
     Human-editable: delete a line to stop the system learning from it. -->

- [S6] reviewer corrected "when" to: Next curriculum cycle - session 3 is not running until March
- [S5] not actionable because: research.trychroma.com is a blog post we cite, not a tool step; docs redirects there are expected
- [S1] reviewer corrected "when" to: Before the March cohort
- [S7] reviewer corrected "what" to: Reword the 16 MCQs to gemini-2.5-flash, which is listed as available on the same Google page and fills the same workhorse role. Google's table has no replacement column, so this successor is our choice, not Google's recommendation - state it that way.
- [S7] reviewer corrected "why" to: Verified independently against Google's own catalogue: 53 rows, 48 available and 4 shut down, and gemini-2.0-flash's OWN row carries '(Shut down)' in its Model cell with the exact id in its Endpoint cell - so this is a per-row read, not text proximity to the adjacent Flash-Lite row. But the standard 'fails at call time, so every example stops working' line is wrong here: all 16 locations are OBJEC
- [S7] reviewer corrected "when" to: Before the next assessment cycle rather than this sprint - nothing breaks at run time, but 9 of the 16 questions sit in Building LLM Applications session 4 alone, so that one session is the whole job.
- [S7] reviewer corrected "what" to: Point the session 10 reading resource at gemini-3.1-pro-preview, which is listed as available on the same page. Preview ids will keep retiring, so prefer naming a GA model in reading material where the example does not need the preview feature.
- [S7] reviewer corrected "why" to: True and verified - gemini-3-pro-preview's own row on Google's models page reads 'Gemini 3 Pro Preview (Shut down)'. A preview id being retired once the GA model lands is routine, which is exactly why severity should follow what the curriculum does with it: one line of reading material, nothing executed, nothing graded.
- [S7] reviewer corrected "when" to: Low priority - a single LEARNING_RESOURCE mention, no graded item, blast radius 2. Critical is mis-ranked for this one; it belongs in the next content cycle's cleanup, not a sprint.
