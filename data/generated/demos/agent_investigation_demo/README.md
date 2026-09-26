# Agent Investigation Acceptance Scenarios

This directory does not duplicate quotation binaries. Instead, its hash-backed manifest reuses synthetic inputs from `full_flow_demo4`. The runtime manifest describes only scenarios and inputs. Expected tool routes are stored in `evaluation/reference/agent_investigation_demo/cases.json` and must not be exposed to the Agent.

For quotation scenarios, create separate tasks using the manifest's `requirement`, `base_quotes`, and `variant_quote`. For user-request scenarios, first complete a comparison with the four primary quotations, then select a target from the Investigation page. Policy failures and invalid inputs are controlled state injections and must not be fabricated as policy or quotation content.
