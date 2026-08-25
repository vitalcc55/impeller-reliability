# Import Security

Future `.r130run` import stages in a bounded temporary directory and rejects absolute/`..`/duplicate/symlink paths, excess files/expanded size/rows, unknown files/schema, invalid UTF-8, NaN/Infinity, checksum/plan mismatch and CSV formula injection. Source package and SHA-256 are retained unchanged. `diagnostic_partial` never enters reliability analysis automatically.
