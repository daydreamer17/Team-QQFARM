# Compliance Review Evidence (Synthetic Demonstration Materials Only)

Use `initial/` for the first review round. Each supplier has one supplier-admission record and one RoHS declaration. Review each source file, select the corresponding supplier and control on the Compliance Review page, enter the values from `entry_guide.json`, and upload the same file.

The first round intentionally includes four states: complete and valid, part-number mismatch, explicitly non-compliant, and expired. Do not treat filenames or this README as evidence. Verify the TXT source before selecting the confirmation that the evidence scope has been reviewed.

Use `corrections/` for the second round. Replace the corresponding earlier record through the page's Replace Evidence action. Do not treat both the old and new files as currently valid; otherwise, the system should identify a conflict or retain the earlier file as a historical version.

`paired_scenarios/` provides a complete matrix of 24 evidence files: four suppliers × supplier admission, RoHS, and amount approval × compliant/non-compliant. Each supplier directory contains six automatically parsable Markdown files. To test one exception, upload only the corresponding file. To test the replacement loop, upload the `non-compliant` file first, then use the matching `compliant` file with Replace Evidence.

The amount-approval rule is triggered only when the selected quotation's total cost reaches SGD 7,000. Amount files for suppliers below the threshold are used for parsing and boundary tests; they do not mean that the business process requires evidence to be uploaded in advance.

These files are used only to demonstrate evidence review and version tracking. They are not real certificates, do not establish the compliance of any real supplier or product, and do not constitute procurement approval.
