# -----------------------------------------------------------------------------
# AI host voice channel (#259 step 6) — OFF BY DEFAULT
#
# Every resource and data source here has `count = var.voice_enabled ? ...`
# (tests/infra/test_terraform_voice_gated.py checks it), so with voice off a
# plan shows nothing from this file and merging creates nothing billable.
#
# Call path: caller -> Amazon Connect number -> contact flow
# (voice/contact_flow.json.tftpl) -> screening Lambda (screen.py, POST
# /api/v1/voice/screen; block -> disconnect) -> Lex V2 bot (HostTurn) ->
# bridge Lambda (infra/voice/bridge) -> POST https://<app host>/api/v1/voice/turn.
#
# The aws provider (5.x) has no Lex V2 bot alias resource and its
# aws_connect_bot_association only takes Lex V1 bots, and Lex cannot publish
# a version until the locale is built. So enabling is two applies around
# terraform/voice/publish_lex_bot.sh (README "Voice channel"):
#   1. apply with voice_enabled=true -> instance, bot, Lambda, secret, budget;
#   2. publish_lex_bot.sh -> fallback code hook, build, version, alias with
#      the Lambda code hook, Connect association; it prints the alias ARN;
#   3. apply with voice_lex_bot_alias_arn=<arn> -> contact flow, and the
#      ECS task gets CONNECT_CONTACT_FLOW_ID.
# -----------------------------------------------------------------------------

locals {
  voice_backend_url   = var.domain_name != "" ? "https://${local.app_host}" : ""
  voice_connect_alias = "${var.app_name}-host"

  # Slot name -> built-in slot type. The bridge elicits all but `utterance`
  # by name (handler._ELICITS); `utterance` is the free-text catch-all.
  voice_slots = {
    party_size        = "AMAZON.Number"
    phone_number      = "AMAZON.PhoneNumber"
    confirmation_code = "AMAZON.AlphaNumeric"
    date              = "AMAZON.Date"
    time              = "AMAZON.Time"
    utterance         = "AMAZON.FreeFormInput"
  }
}

# --- Bridge secret -----------------------------------------------------------
# Shared by the Lambda (X-Voice-Bridge-Secret) and the ECS task
# (VOICE_BRIDGE_SECRET). Seeded with a random value from Secrets Manager,
# never a committed placeholder: a public placeholder would open the bridge
# endpoint to anyone who reads this repo. ECS needs a value to start a task
# that references the secret. Rotate with put-secret-value, then redeploy the
# service; the Lambda refetches on its next 404.

resource "aws_secretsmanager_secret" "voice_bridge" {
  count = var.voice_enabled ? 1 : 0

  name                    = "${var.app_name}/${var.environment}/VOICE_BRIDGE_SECRET"
  description             = "Shared secret between the voice bridge Lambda and the backend"
  recovery_window_in_days = 7

  tags = { Name = "${var.app_name}-voice-bridge-secret" }
}

data "aws_secretsmanager_random_password" "voice_bridge" {
  count = var.voice_enabled ? 1 : 0

  password_length     = 48
  exclude_punctuation = true
}

resource "aws_secretsmanager_secret_version" "voice_bridge" {
  count = var.voice_enabled ? 1 : 0

  secret_id     = aws_secretsmanager_secret.voice_bridge[0].id
  secret_string = data.aws_secretsmanager_random_password.voice_bridge[0].random_password

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# --- Bridge Lambda ------------------------------------------------------------

data "aws_iam_policy_document" "voice_lambda_assume" {
  count = var.voice_enabled ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "voice_lambda" {
  count = var.voice_enabled ? 1 : 0

  name               = "${var.app_name}-voice-bridge-lambda"
  assume_role_policy = data.aws_iam_policy_document.voice_lambda_assume[0].json

  tags = { Name = "${var.app_name}-voice-bridge-lambda" }
}

resource "aws_cloudwatch_log_group" "voice_lambda" {
  count = var.voice_enabled ? 1 : 0

  name              = "/aws/lambda/${var.app_name}-voice-bridge"
  retention_in_days = 14

  tags = { Name = "${var.app_name}-voice-bridge-logs" }
}

data "aws_iam_policy_document" "voice_lambda" {
  count = var.voice_enabled ? 1 : 0

  statement {
    sid     = "Logs"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "${aws_cloudwatch_log_group.voice_lambda[0].arn}:*",
      "${aws_cloudwatch_log_group.voice_screen[0].arn}:*",
    ]
  }

  statement {
    sid       = "ReadBridgeSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.voice_bridge[0].arn]
  }
}

resource "aws_iam_role_policy" "voice_lambda" {
  count = var.voice_enabled ? 1 : 0

  name   = "${var.app_name}-voice-bridge-lambda"
  role   = aws_iam_role.voice_lambda[0].id
  policy = data.aws_iam_policy_document.voice_lambda[0].json
}

resource "aws_lambda_function" "voice_bridge" {
  count = var.voice_enabled ? 1 : 0

  function_name    = "${var.app_name}-voice-bridge"
  description      = "Lex V2 code hook: one phone turn -> POST /api/v1/voice/turn"
  role             = aws_iam_role.voice_lambda[0].arn
  runtime          = "python3.11"
  handler          = "handler.handler"
  memory_size      = 256
  timeout          = 10
  filename         = var.voice_bridge_zip
  source_code_hash = filebase64sha256(var.voice_bridge_zip)

  environment {
    variables = {
      BACKEND_URL = local.voice_backend_url
      SECRET_ARN  = aws_secretsmanager_secret.voice_bridge[0].arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.voice_lambda, aws_iam_role_policy.voice_lambda]

  tags = { Name = "${var.app_name}-voice-bridge" }
}

# Any alias of this bot may invoke the Lambda; the alias itself is created by
# publish_lex_bot.sh (no provider resource), so its id is not known here.
resource "aws_lambda_permission" "voice_lex" {
  count = var.voice_enabled ? 1 : 0

  statement_id  = "AllowLexV2BotAlias"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.voice_bridge[0].function_name
  principal     = "lexv2.amazonaws.com"
  source_arn    = "arn:aws:lex:${var.aws_region}:${data.aws_caller_identity.current.account_id}:bot-alias/${aws_lexv2models_bot.voice[0].id}/*"
}

# --- Screening Lambda (#259 step 7) -----------------------------------------
# Same zip, role, secret and environment as the bridge. The contact flow
# invokes it before the Lex bot and disconnects only on {"action": "block"};
# any error or timeout answers the call (screen.py fails open). 512 MB buys
# the CPU for a fast cold start (boto3 and the secret load at init, which
# has its own budget); the 3 s timeout stays the per-call bound.

resource "aws_cloudwatch_log_group" "voice_screen" {
  count = var.voice_enabled ? 1 : 0

  name              = "/aws/lambda/${var.app_name}-voice-screen"
  retention_in_days = 14

  tags = { Name = "${var.app_name}-voice-screen-logs" }
}

resource "aws_lambda_function" "voice_screen" {
  count = var.voice_enabled ? 1 : 0

  function_name    = "${var.app_name}-voice-screen"
  description      = "Connect call screening: POST /api/v1/voice/screen -> answer or block"
  role             = aws_iam_role.voice_lambda[0].arn
  runtime          = "python3.11"
  handler          = "screen.handler"
  memory_size      = 512
  timeout          = 3
  filename         = var.voice_bridge_zip
  source_code_hash = filebase64sha256(var.voice_bridge_zip)

  environment {
    variables = {
      BACKEND_URL = local.voice_backend_url
      SECRET_ARN  = aws_secretsmanager_secret.voice_bridge[0].arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.voice_screen, aws_iam_role_policy.voice_lambda]

  tags = { Name = "${var.app_name}-voice-screen" }
}

resource "aws_lambda_permission" "voice_screen_connect" {
  count = var.voice_enabled ? 1 : 0

  statement_id  = "AllowConnectInstance"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.voice_screen[0].function_name
  principal     = "connect.amazonaws.com"
  source_arn    = aws_connect_instance.voice[0].arn
}

# Connect may only invoke Lambdas associated with the instance.
resource "aws_connect_lambda_function_association" "voice_screen" {
  count = var.voice_enabled ? 1 : 0

  instance_id  = aws_connect_instance.voice[0].id
  function_arn = aws_lambda_function.voice_screen[0].arn
}

# --- Lex V2 bot -------------------------------------------------------------

data "aws_iam_policy_document" "voice_lex_assume" {
  count = var.voice_enabled ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lexv2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "voice_lex" {
  count = var.voice_enabled ? 1 : 0

  name               = "${var.app_name}-voice-lex-bot"
  assume_role_policy = data.aws_iam_policy_document.voice_lex_assume[0].json

  tags = { Name = "${var.app_name}-voice-lex-bot" }
}

resource "aws_iam_role_policy" "voice_lex" {
  count = var.voice_enabled ? 1 : 0

  name = "${var.app_name}-voice-lex-bot"
  role = aws_iam_role.voice_lex[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "Polly"
      Effect   = "Allow"
      Action   = ["polly:SynthesizeSpeech"]
      Resource = "*"
    }]
  })
}

resource "aws_lexv2models_bot" "voice" {
  count = var.voice_enabled ? 1 : 0

  name                        = "${var.app_name}-host"
  description                 = "AI reservation host (#259); every turn goes to the bridge Lambda"
  role_arn                    = aws_iam_role.voice_lex[0].arn
  idle_session_ttl_in_seconds = 300

  data_privacy {
    child_directed = false
  }

  tags = { Name = "${var.app_name}-host" }
}

resource "aws_lexv2models_bot_locale" "voice" {
  count = var.voice_enabled ? 1 : 0

  bot_id                           = aws_lexv2models_bot.voice[0].id
  bot_version                      = "DRAFT"
  locale_id                        = "en_US"
  n_lu_intent_confidence_threshold = 0.40

  voice_settings {
    voice_id = var.voice_polly_voice
    engine   = "neural"
  }
}

# One catch-all intent: the bridge, not Lex, understands the caller. Sample
# utterances only steer Lex toward HostTurn; anything else lands on
# FallbackIntent, whose code hook publish_lex_bot.sh turns on (the provider
# cannot manage the built-in fallback intent Lex creates with the locale).
resource "aws_lexv2models_intent" "voice_host_turn" {
  count = var.voice_enabled ? 1 : 0

  bot_id      = aws_lexv2models_bot.voice[0].id
  bot_version = "DRAFT"
  locale_id   = aws_lexv2models_bot_locale.voice[0].locale_id
  name        = "HostTurn"
  description = "Every caller turn; the bridge Lambda answers it"

  dynamic "sample_utterance" {
    for_each = [
      "hi",
      "hello",
      "I'd like to book a table",
      "I want to make a reservation",
      "book a table",
      "a table for two",
      "can I get a table tonight",
      "cancel my reservation",
      "change my reservation",
      "I need to change my booking",
      "are you open",
      "what are your hours",
      "do you have anything available",
      "yes",
      "no",
      "tomorrow",
      "tonight at seven",
      "my confirmation code is",
      "my number is",
      "can I talk to someone",
    ]
    content {
      utterance = sample_utterance.value
    }
  }

  dialog_code_hook {
    enabled = true
  }

  fulfillment_code_hook {
    enabled = true
  }
}

resource "aws_lexv2models_slot" "voice" {
  for_each = var.voice_enabled ? local.voice_slots : {}

  bot_id       = aws_lexv2models_bot.voice[0].id
  bot_version  = "DRAFT"
  locale_id    = aws_lexv2models_bot_locale.voice[0].locale_id
  intent_id    = aws_lexv2models_intent.voice_host_turn[0].intent_id
  name         = each.key
  slot_type_id = each.value

  # Optional: the bridge elicits a slot (with its own SSML prompt) only when
  # the backend asks for one. This prompt is Lex's required placeholder.
  value_elicitation_setting {
    slot_constraint = "Optional"

    prompt_specification {
      max_retries                = 1
      allow_interrupt            = true
      message_selection_strategy = "Ordered"

      message_group {
        message {
          plain_text_message {
            value = "Sorry, could you say that again?"
          }
        }
      }
    }
  }
}

# --- Amazon Connect -----------------------------------------------------------

# Phone numbers are never logged. The contact flow switches its own flow
# logging off as its first block (voice/contact_flow.json.tftpl,
# UpdateFlowLoggingBehavior), before any block that carries the caller
# number (the screen Lambda's input, the bot's `caller` session attribute).
# Connect has no log level that keeps blocks but drops customer attributes,
# so the switch is all or nothing. As a backstop for whatever Connect writes
# before that block runs, the group Connect writes to
# (/aws/connect/<instance alias>) is created here first with a 1-day
# retention, the shortest CloudWatch offers.
resource "aws_cloudwatch_log_group" "voice_connect_flows" {
  count = var.voice_enabled ? 1 : 0

  name              = "/aws/connect/${local.voice_connect_alias}"
  retention_in_days = 1

  tags = { Name = "${var.app_name}-host-flow-logs" }
}

resource "aws_connect_instance" "voice" {
  count = var.voice_enabled ? 1 : 0

  instance_alias           = local.voice_connect_alias
  identity_management_type = "CONNECT_MANAGED"
  inbound_calls_enabled    = true
  # The handoff transfers to the location's external staff number, which
  # Connect places as an outbound call. Nothing is billed unless used.
  outbound_calls_enabled    = true
  contact_lens_enabled      = false
  contact_flow_logs_enabled = true

  tags = { Name = "${var.app_name}-host" }

  depends_on = [aws_cloudwatch_log_group.voice_connect_flows]
}

# Created on the second apply, once publish_lex_bot.sh has made the alias.
resource "aws_connect_contact_flow" "voice" {
  count = var.voice_enabled && var.voice_lex_bot_alias_arn != "" ? 1 : 0

  instance_id = aws_connect_instance.voice[0].id
  name        = "${var.app_name}-host"
  description = "Inbound: screen the caller, then Lex V2 AI host, then transfer to staff when the backend hands off"
  type        = "CONTACT_FLOW"
  content = templatefile("${path.module}/voice/contact_flow.json.tftpl", {
    lex_bot_alias_arn = var.voice_lex_bot_alias_arn
    polly_voice       = var.voice_polly_voice
    screen_lambda_arn = aws_lambda_function.voice_screen[0].arn
  })

  depends_on = [aws_connect_lambda_function_association.voice_screen]

  tags = { Name = "${var.app_name}-host" }
}

# --- ECS task permissions for number provisioning (backend/services/connect_numbers.py) ---

data "aws_iam_policy_document" "voice_ecs_connect" {
  count = var.voice_enabled ? 1 : 0

  statement {
    sid = "SearchAndList"
    actions = [
      "connect:SearchAvailablePhoneNumbers",
      "connect:ListPhoneNumbersV2",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "AssociateFlow"
    actions = ["connect:AssociatePhoneNumberContactFlow"]
    resources = [
      "${aws_connect_instance.voice[0].arn}/contact-flow/*",
      "arn:aws:connect:${var.aws_region}:${data.aws_caller_identity.current.account_id}:phone-number/*",
    ]
  }

  # Claim takes the instance as TargetArn; scoped to this instance and
  # account phone numbers. Unverified against live IAM evaluation: widen to
  # "*" for these two actions if ClaimPhoneNumber is denied.
  statement {
    sid = "ClaimRelease"
    actions = [
      "connect:ClaimPhoneNumber",
      "connect:ReleasePhoneNumber",
    ]
    resources = [
      aws_connect_instance.voice[0].arn,
      "${aws_connect_instance.voice[0].arn}/*",
      "arn:aws:connect:${var.aws_region}:${data.aws_caller_identity.current.account_id}:phone-number/*",
    ]
  }
}

resource "aws_iam_role_policy" "voice_ecs_connect" {
  count = var.voice_enabled ? 1 : 0

  name   = "${var.app_name}-voice-connect-numbers"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.voice_ecs_connect[0].json
}

# --- ECS task permissions for call capacity (backend/services/voice_quotas.py) ---
# Decision 103. Service Quotas reads and CloudWatch GetMetricStatistics take
# no resource-level permissions or service conditions, so they are "*"; the
# one write, RequestServiceQuotaIncrease, is limited to the connect service
# (servicequotas:service). Quota changes are free and only go up; the AWS
# Support API is never granted. Unverified against live IAM evaluation: if
# the request is denied, drop the condition and say so in the README.

data "aws_iam_policy_document" "voice_ecs_quotas" {
  count = var.voice_enabled ? 1 : 0

  statement {
    sid = "ReadQuotasAndCallMetrics"
    actions = [
      "servicequotas:GetServiceQuota",
      "servicequotas:ListServiceQuotas",
      "servicequotas:ListRequestedServiceQuotaChangeHistoryByQuota",
      "cloudwatch:GetMetricStatistics",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "RequestConnectQuotaIncrease"
    actions   = ["servicequotas:RequestServiceQuotaIncrease"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "servicequotas:service"
      values   = ["connect"]
    }
  }
}

resource "aws_iam_role_policy" "voice_ecs_quotas" {
  count = var.voice_enabled ? 1 : 0

  name   = "${var.app_name}-voice-call-capacity"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.voice_ecs_quotas[0].json
}

# Concurrent calls above 80% of the instance's quota: raise it before calls
# are refused (the daily capacity step also does, decision 103).
resource "aws_cloudwatch_metric_alarm" "voice_concurrent_calls" {
  count = var.voice_enabled ? 1 : 0

  alarm_name          = "${var.app_name}-voice-concurrent-calls"
  alarm_description   = "Amazon Connect concurrent calls above 80% of the instance quota"
  namespace           = "AWS/Connect"
  metric_name         = "ConcurrentCallsPercentage"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    InstanceId  = aws_connect_instance.voice[0].id
    MetricGroup = "VoiceCalls"
  }
  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]
}

# --- Budget -------------------------------------------------------------------
# Alerts, never stops anything: a budget cannot cap spend.

resource "aws_budgets_budget" "voice" {
  count = var.voice_enabled ? 1 : 0

  name         = "${var.app_name}-voice-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.voice_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "Service"
    values = ["Amazon Connect", "Amazon Lex", "Amazon Polly", "AWS Lambda"]
  }

  # A notification needs a subscriber; with no ops_alert_email it is omitted.
  dynamic "notification" {
    for_each = var.ops_alert_email != "" ? [var.ops_alert_email] : []
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = 80
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [notification.value]
    }
  }
}
