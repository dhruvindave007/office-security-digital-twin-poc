$input = [Console]::In.ReadToEnd()
[Console]::Out.WriteLine((@{
  injectSteps = @(@{
    ephemeralMessage = 'Project protocol: OBSERVE → PLAN → EXECUTE → VERIFY → REVIEW → REPORT. Do not claim completion without actual verification. Do not make unapproved architecture changes. Prefer official AWS/Antigravity documentation for current facts.'
  })
} | ConvertTo-Json -Compress -Depth 5))
