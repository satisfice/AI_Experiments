@echo off
jq --arg animal "%~1" --arg prompt "%~2" --arg prompt2 "%~3" "{($prompt): [.[][] | select(.metadata.prompt == $prompt) | .items[] | select(. == $animal)] | length, (($prompt2)): [.[][] | select(.metadata.prompt == ($prompt2)) | .items[] | select(. == $animal)] | length}" results.json
