<template>
  <div class="flex h-full flex-col gap-6 px-6 py-8 text-ink-gray-8">
    <!-- Header -->
    <div class="flex justify-between px-2">
      <div class="flex flex-col gap-1">
        <h2 class="flex gap-2 text-2xl-semibold leading-none h-5">
          {{ __('AI & Follow-ups') }}
        </h2>
        <p class="text-p-base text-ink-gray-6">
          {{
            __(
              'Plug in an AI key and set the WhatsApp and email follow-up sequence for leads who stop replying',
            )
          }}
        </p>
      </div>
      <div class="flex item-center space-x-2 justify-end">
        <Button
          :label="__('Update')"
          variant="solid"
          :loading="saving"
          @click="save"
        />
      </div>
    </div>

    <div class="flex flex-1 flex-col gap-6 overflow-y-auto px-2">
      <!-- AI provider -->
      <section class="flex flex-col gap-3">
        <div>
          <div class="text-p-base-medium text-ink-gray-7">
            {{ __('AI Provider') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'The key is stored encrypted on the server and is never sent to the browser',
              )
            }}
          </div>
        </div>

        <div v-if="ai.doc" class="flex flex-col gap-3">
          <div class="flex items-center justify-between gap-8">
            <div class="text-p-base text-ink-gray-7">{{ __('Enabled') }}</div>
            <FormControl v-model="ai.doc.enabled" type="checkbox" />
          </div>
          <div class="flex items-center justify-between gap-8">
            <div class="text-p-base text-ink-gray-7">{{ __('Provider') }}</div>
            <FormControl
              v-model="ai.doc.provider"
              type="select"
              class="w-56"
              :options="providerOptions"
            />
          </div>
          <div class="flex items-center justify-between gap-8">
            <div class="text-p-base text-ink-gray-7">{{ __('Model') }}</div>
            <FormControl
              v-model="ai.doc.model"
              type="text"
              class="w-56"
              :placeholder="suggestedModel"
            />
          </div>
          <div class="flex items-center justify-between gap-8">
            <div class="text-p-base text-ink-gray-7">{{ __('API Key') }}</div>
            <FormControl
              v-model="apiKey"
              type="password"
              class="w-56"
              autocomplete="off"
              :placeholder="
                apiKeyIsSet ? __('Stored — type to replace') : __('Paste key')
              "
            />
          </div>
          <div class="flex items-center justify-between gap-8">
            <div class="text-p-base text-ink-gray-7">
              {{ __('Max requests per month') }}
              <span class="text-p-sm text-ink-gray-5">
                {{ __('(0 means unlimited)') }}
              </span>
            </div>
            <FormControl
              v-model.number="ai.doc.max_monthly_requests"
              type="number"
              class="w-56"
              :min="0"
            />
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{ __('Used this month: {0}', [ai.doc.requests_this_month || 0]) }}
          </div>
        </div>
      </section>

      <div class="h-px border-t border-outline-elevation-2" />

      <!-- Follow-up sequence -->
      <section v-if="followup.doc" class="flex flex-col gap-3">
        <div>
          <div class="text-p-base-medium text-ink-gray-7">
            {{ __('Follow-ups') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'Outside the 24 hour reply window WhatsApp accepts approved templates only, so every WhatsApp stage sends a template. These guardrails apply to both channels',
              )
            }}
          </div>
        </div>

        <div class="flex items-center justify-between gap-8">
          <div class="text-p-base text-ink-gray-7">{{ __('Enabled') }}</div>
          <FormControl v-model="followup.doc.enabled" type="checkbox" />
        </div>
        <div class="flex items-center justify-between gap-8">
          <div class="text-p-base text-ink-gray-7">
            {{ __('Enroll conversations automatically') }}
          </div>
          <FormControl v-model="followup.doc.auto_enroll" type="checkbox" />
        </div>
        <div class="flex items-center justify-between gap-8">
          <div class="text-p-base text-ink-gray-7">{{ __('Send Mode') }}</div>
          <FormControl
            v-model="followup.doc.send_mode"
            type="select"
            class="w-56"
            :options="sendModeOptions"
          />
        </div>
        <div class="flex items-center justify-between gap-8">
          <div class="text-p-base text-ink-gray-7">{{ __('Quiet Hours') }}</div>
          <div class="flex items-center gap-2">
            <FormControl
              v-model="followup.doc.quiet_hours_start"
              type="time"
              class="w-28"
            />
            <span class="text-p-sm text-ink-gray-5">{{ __('to') }}</span>
            <FormControl
              v-model="followup.doc.quiet_hours_end"
              type="time"
              class="w-28"
            />
          </div>
        </div>
        <div class="flex items-center justify-between gap-8">
          <div class="text-p-base text-ink-gray-7">
            {{ __('Daily Send Cap') }}
          </div>
          <FormControl
            v-model.number="followup.doc.daily_send_cap"
            type="number"
            class="w-56"
            :min="0"
          />
        </div>
        <div class="flex items-center justify-between gap-8">
          <div class="flex flex-col">
            <div class="text-p-base text-ink-gray-7">
              {{ __('Ignore threads idle longer than') }}
            </div>
            <div class="text-p-sm text-ink-gray-5">
              {{
                __(
                  'Stops the first run from draining a months-old backlog. 0 means no bound',
                )
              }}
            </div>
          </div>
          <FormControl
            v-model.number="followup.doc.ignore_older_than_days"
            type="number"
            class="w-56"
            :min="0"
          />
        </div>
        <div class="flex items-start justify-between gap-8">
          <div class="flex flex-col">
            <div class="text-p-base text-ink-gray-7">
              {{ __('Stop Keywords') }}
            </div>
            <div class="text-p-sm text-ink-gray-5">
              {{
                __(
                  'One per line. A reply that contains one stops the sequence for good',
                )
              }}
            </div>
          </div>
          <FormControl
            v-model="followup.doc.stop_keywords"
            type="textarea"
            class="w-56"
            :rows="4"
          />
        </div>
      </section>

      <div class="h-px border-t border-outline-elevation-2" />

      <!-- Stages -->
      <section v-if="followup.doc" class="flex flex-col gap-3">
        <div>
          <div class="text-p-base-medium text-ink-gray-7">
            {{ __('Stages') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'Silence days count from the previous message, so 2 / 2 / 2 sends on day 2, day 4 and day 6',
              )
            }}
          </div>
        </div>

        <div
          v-for="(stage, index) in followup.doc.stages || []"
          :key="index"
          class="flex flex-col gap-2 rounded-lg border border-outline-gray-2 p-3"
        >
          <div class="flex items-center gap-2">
            <div class="text-p-base-medium text-ink-gray-7 w-20 shrink-0">
              {{ __('Stage {0}', [index + 1]) }}
            </div>
            <FormControl
              v-model.number="stage.silence_days"
              type="number"
              class="w-20 shrink-0"
              :min="1"
            />
            <span class="text-p-sm text-ink-gray-5 shrink-0">
              {{ __('days of silence') }}
            </span>
            <FormControl
              :modelValue="stage.channel || 'WhatsApp'"
              type="select"
              class="w-32 shrink-0"
              :options="channelOptions"
              @update:modelValue="(value) => (stage.channel = value)"
            />
            <!-- One picker slot, whichever channel the stage is on. Two pickers
                 side by side would be a settings screen that asks the manager to
                 ignore half of it (§2). -->
            <Autocomplete
              v-if="!isEmail(stage)"
              class="flex-1"
              :modelValue="stage.template"
              :options="templateOptions"
              :placeholder="__('Approved template')"
              @update:modelValue="(option) => (stage.template = option?.value)"
            />
            <Autocomplete
              v-else
              class="flex-1"
              :modelValue="stage.email_template"
              :options="emailTemplateOptions"
              :placeholder="__('Email template')"
              @update:modelValue="
                (option) => (stage.email_template = option?.value)
              "
            />
            <Button
              icon="lucide-x"
              variant="ghost"
              @click="followup.doc.stages.splice(index, 1)"
            />
          </div>
          <div v-if="isEmail(stage)" class="flex items-center gap-2">
            <div class="text-p-sm text-ink-gray-5 w-20 shrink-0">
              {{ __('Subject') }}
            </div>
            <FormControl
              v-model="stage.email_subject_override"
              type="text"
              class="flex-1"
              :placeholder="__(`Leave empty to use the template's own subject`)"
            />
          </div>
          <div v-else class="flex items-center gap-2">
            <div class="text-p-sm text-ink-gray-5 w-20 shrink-0">
              {{ __('Use AI') }}
            </div>
            <FormControl v-model="stage.use_ai" type="checkbox" />
            <FormControl
              v-if="stage.use_ai"
              v-model="stage.ai_instruction"
              type="text"
              class="flex-1"
              :placeholder="__('warm nudge referencing their destination')"
            />
          </div>
        </div>

        <div v-if="!templateOptions.length" class="text-p-sm text-ink-gray-5">
          {{
            __(
              'No approved WhatsApp templates found. Sync templates from Meta first — a stage without an approved template is skipped',
            )
          }}
        </div>

        <div v-if="hasEmailStage" class="text-p-sm text-ink-gray-5">
          {{
            __(
              'Email stages send only while both "Email sequences" and "Outbound engine" are on in Settings → Feature Flags. Every sequence email carries an unsubscribe link',
            )
          }}
        </div>

        <Button
          class="w-fit"
          :label="__('Add Stage')"
          iconLeft="plus"
          @click="addStage"
        />
      </section>
    </div>

    <ErrorMessage :message="errorMessage" />
  </div>
</template>

<script setup>
import Autocomplete from '@/components/frappe-ui/Autocomplete.vue'
import {
  createDocumentResource,
  createResource,
  Button,
  ErrorMessage,
  FormControl,
  toast,
} from 'frappe-ui'
import { computed, ref } from 'vue'

const apiKey = ref('')
const errorMessage = ref('')

const providerOptions = [
  { label: 'Anthropic', value: 'Anthropic' },
  { label: 'OpenAI', value: 'OpenAI' },
  { label: 'OpenRouter', value: 'OpenRouter' },
  { label: 'Google Gemini', value: 'Gemini' },
]

// A hint only. It fills the empty input's placeholder; the agency still types
// whichever model id it wants.
const suggestedModels = {
  Anthropic: 'claude-sonnet-5',
  OpenAI: 'gpt-5',
  OpenRouter: 'anthropic/claude-sonnet-5',
  Gemini: 'gemini-2.0-flash',
}

const sendModeOptions = computed(() => [
  { label: __('Auto-send'), value: 'Auto-send' },
  { label: __('Draft for approval'), value: 'Draft for approval' },
])

// The values are the doctype's own Select options and are NOT translated. The
// labels are.
const channelOptions = computed(() => [
  { label: __('WhatsApp'), value: 'WhatsApp' },
  { label: __('Email'), value: 'Email' },
])

const ai = createDocumentResource({
  doctype: 'CRM AI Settings',
  name: 'CRM AI Settings',
  fields: ['*'],
  auto: true,
})

const followup = createDocumentResource({
  doctype: 'CRM Followup Settings',
  name: 'CRM Followup Settings',
  fields: ['*'],
  auto: true,
})

const templates = createResource({
  url: 'crm.api.followup_engine.get_template_options',
  auto: true,
})

const emailTemplates = createResource({
  url: 'crm.api.followup_engine.get_email_template_options',
  auto: true,
})

const templateOptions = computed(() =>
  (templates.data || []).map((template) => ({
    label: template.template_name || template.name,
    value: template.name,
  })),
)

const emailTemplateOptions = computed(() =>
  (emailTemplates.data || []).map((template) => ({
    label: template.subject
      ? `${template.name} — ${template.subject}`
      : template.name,
    value: template.name,
  })),
)

const hasEmailStage = computed(() =>
  (followup.doc?.stages || []).some((stage) => isEmail(stage)),
)

// An empty channel means WhatsApp: every stage saved before Stage 5.1 has one,
// and the server reads it the same way (`crm.api.followup_engine.get_stages`).
function isEmail(stage) {
  return (stage?.channel || 'WhatsApp') === 'Email'
}

const suggestedModel = computed(
  () => suggestedModels[ai.doc?.provider] || suggestedModels.Anthropic,
)

// A stored key comes back masked, so the input stays empty and only a typed
// value is sent back to the server.
const apiKeyIsSet = computed(() => Boolean(ai.doc?.api_key))

const saving = computed(
  () => Boolean(ai.save?.loading) || Boolean(followup.save?.loading),
)

function addStage() {
  if (!followup.doc.stages) followup.doc.stages = []
  followup.doc.stages.push({
    stage_number: followup.doc.stages.length + 1,
    silence_days: 2,
    channel: 'WhatsApp',
    use_ai: 0,
  })
}

function save() {
  errorMessage.value = ''

  if (apiKey.value) {
    ai.doc.api_key = apiKey.value
  } else {
    delete ai.doc.api_key
  }

  ;(followup.doc.stages || []).forEach((stage, index) => {
    stage.stage_number = index + 1
  })

  Promise.all([ai.save.submit(), followup.save.submit()])
    .then(() => {
      apiKey.value = ''
      toast.success(__('Updated successfully'))
    })
    .catch((error) => {
      errorMessage.value = error?.messages?.[0] || error?.message
    })
}
</script>
