$input = [Console]::In.ReadToEnd()
try { $payload = $input | ConvertFrom-Json } catch {
  '{"decision":"ask","reason":"Unable to parse hook input safely."}'
  exit 0
}

$tool = $payload.toolCall.name
$cmd = ''
if ($payload.toolCall.args.CommandLine) { $cmd = [string]$payload.toolCall.args.CommandLine }

if ($tool -ne 'run_command') {
  '{"decision":"allow"}'
  exit 0
}

$danger = @(
  'git\s+reset\s+--hard',
  'git\s+clean\s+-fd',
  'Remove-Item\s+.*-Recurse.*-Force',
  'rm\s+-rf\s+/',
  'rmdir\s+/s\s+/q',
  'del\s+/s\s+/q',
  'format\s+[A-Za-z]:',
  'shutdown\s+',
  'reboot\s+',
  'sudo\s+',
  'aws\s+(iam|organizations|account|cloudformation|ec2|s3\s+rb|s3\s+rm)\b',
  'terraform\s+apply\b',
  'terraform\s+destroy\b',
  'cdk\s+deploy\b',
  'pulumi\s+up\b'
)

foreach ($pattern in $danger) {
  if ($cmd -match $pattern) {
    $reason = "Safety gate: potentially destructive or infrastructure-changing command requires explicit approval."
    [Console]::Out.WriteLine((@{decision='force_ask'; reason=$reason} | ConvertTo-Json -Compress))
    exit 0
  }
}

[Console]::Out.WriteLine('{"decision":"allow"}')
