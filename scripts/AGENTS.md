# Script rules

PowerShell scripts являются тонким Windows-native control plane. Они сохраняют exit codes, используют bounded waits и предсказуемые artifact paths, не скрывают underlying tools и не мутируют production. Packaged smoke проверяет настоящий EXE, no TCP и отсутствие orphan worker. Новая оболочка добавляется только для повторяемой хрупкой последовательности.
