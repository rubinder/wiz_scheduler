#!/usr/bin/env bash
# One-time (and after every bot change) publish of the voice Lex bot (#259).
#
# The aws provider 5.x cannot do these, so terraform/voice.tf leaves them here:
#   1. turn on the dialog and fulfillment code hooks of the built-in
#      FallbackIntent (Lex creates it with the locale; terraform cannot adopt it);
#   2. build the en_US locale and wait for it;
#   3. publish a bot version from DRAFT;
#   4. create (or point) the "live" alias at that version with the bridge
#      Lambda as its en_US code hook;
#   5. associate the alias with the Connect instance.
# It prints the alias ARN; apply again with
#   -var voice_lex_bot_alias_arn=<that ARN>
# (TF_VAR_voice_lex_bot_alias_arn in .github/workflows/deploy.yml) to create
# the contact flow.
#
# Run after the first `terraform apply -var voice_enabled=true`, from
# terraform/ with the same AWS credentials:
#   ./voice/publish_lex_bot.sh
# Needs only the aws CLI v2 (no jq).
set -euo pipefail

APP_NAME="${APP_NAME:-wizscheduler}"
LOCALE="en_US"
ALIAS_NAME="live"

bot_id=$(aws lexv2-models list-bots \
  --filters "name=BotName,values=${APP_NAME}-host,operator=EQ" \
  --query 'botSummaries[0].botId' --output text)
lambda_arn=$(aws lambda get-function --function-name "${APP_NAME}-voice-bridge" \
  --query 'Configuration.FunctionArn' --output text)
instance_id=$(aws connect list-instances \
  --query "InstanceSummaryList[?InstanceAlias=='${APP_NAME}-host'].Id | [0]" --output text)
if [[ "$bot_id" == "None" || "$instance_id" == "None" ]]; then
  echo "voice resources not found; apply with voice_enabled=true first" >&2
  exit 1
fi

echo "1/5 FallbackIntent code hooks"
fallback_id=$(aws lexv2-models list-intents --bot-id "$bot_id" --bot-version DRAFT \
  --locale-id "$LOCALE" \
  --filters "name=IntentName,values=FallbackIntent,operator=EQ" \
  --query 'intentSummaries[0].intentId' --output text)
aws lexv2-models update-intent --bot-id "$bot_id" --bot-version DRAFT \
  --locale-id "$LOCALE" --intent-id "$fallback_id" \
  --intent-name FallbackIntent --parent-intent-signature AMAZON.FallbackIntent \
  --dialog-code-hook enabled=true \
  --fulfillment-code-hook enabled=true >/dev/null

echo "2/5 build $LOCALE"
aws lexv2-models build-bot-locale --bot-id "$bot_id" --bot-version DRAFT \
  --locale-id "$LOCALE" >/dev/null
aws lexv2-models wait bot-locale-built --bot-id "$bot_id" --bot-version DRAFT \
  --locale-id "$LOCALE"

echo "3/5 publish version"
version=$(aws lexv2-models create-bot-version --bot-id "$bot_id" \
  --bot-version-locale-specification "{\"${LOCALE}\":{\"sourceBotVersion\":\"DRAFT\"}}" \
  --query botVersion --output text)
aws lexv2-models wait bot-version-available --bot-id "$bot_id" --bot-version "$version"

echo "4/5 alias $ALIAS_NAME -> version $version"
locale_settings="{\"${LOCALE}\":{\"enabled\":true,\"codeHookSpecification\":{\"lambdaCodeHook\":{\"lambdaARN\":\"${lambda_arn}\",\"codeHookInterfaceVersion\":\"1.0\"}}}}"
alias_id=$(aws lexv2-models list-bot-aliases --bot-id "$bot_id" \
  --query "botAliasSummaries[?botAliasName=='${ALIAS_NAME}'].botAliasId | [0]" --output text)
if [[ "$alias_id" == "None" ]]; then
  alias_id=$(aws lexv2-models create-bot-alias --bot-id "$bot_id" \
    --bot-alias-name "$ALIAS_NAME" --bot-version "$version" \
    --bot-alias-locale-settings "$locale_settings" \
    --query botAliasId --output text)
else
  aws lexv2-models update-bot-alias --bot-id "$bot_id" --bot-alias-id "$alias_id" \
    --bot-alias-name "$ALIAS_NAME" --bot-version "$version" \
    --bot-alias-locale-settings "$locale_settings" >/dev/null
fi
aws lexv2-models wait bot-alias-available --bot-id "$bot_id" --bot-alias-id "$alias_id"

region=$(aws configure get region || echo us-east-1)
account=$(aws sts get-caller-identity --query Account --output text)
alias_arn="arn:aws:lex:${region}:${account}:bot-alias/${bot_id}/${alias_id}"

echo "5/5 associate with Connect"
aws connect associate-bot --instance-id "$instance_id" \
  --lex-v2-bot "AliasArn=${alias_arn}" 2>/dev/null \
  || echo "   (already associated)"

echo
echo "voice_lex_bot_alias_arn = ${alias_arn}"
