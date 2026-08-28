# Import Security

Future `.r130run` import stages in a bounded temporary directory and rejects absolute/`..`/duplicate/symlink paths, excess files/expanded size/rows, unknown files/schema, invalid UTF-8, NaN/Infinity, checksum/imported run-plan mismatch and CSV formula injection. Source package and SHA-256 are retained unchanged. `diagnostic_partial` never enters reliability analysis automatically.

M02.2B managed CaseDocument attach is not `.r130run` import and does not share its staging/parser model. It accepts only `.pdf`, `.docx`, `.xlsx`, `.csv`, `.json`, `.txt`, `.png`, `.jpg`, `.jpeg` up to 100 MiB, repeats signature/UTF-8 checks in Python, streams into `assets/documents` staging and registers an immutable SHA-256 copy. Archive does not delete content; verify/open never trusts a Renderer path. Document contents are not parsed, indexed, logged, audited or committed to the public repository.
