<template>
  <WorkflowRules v-if="step.screen === 'list'" />
  <WorkflowRuleView
    v-else-if="step.screen === 'view'"
    :key="step.data || 'new'"
  />
</template>

<script setup>
/**
 * Settings -> Automation -> Workflow rules.
 *
 * Two screens and nothing else: the list, and one rule. The same `step`
 * provide/inject the assignment-rule settings page uses, so a reader who knows
 * one knows both. Deliberately NOT a route: everything in Settings lives inside
 * the settings dialog, and a route here would be the only page that escaped it.
 */
import { ref, provide } from 'vue'
import WorkflowRules from './WorkflowRules.vue'
import WorkflowRuleView from './WorkflowRuleView.vue'

const step = ref({ screen: 'list', data: null })

provide('step', step)
provide('updateStep', updateStep)

function updateStep(screen, data) {
  step.value = { screen, data }
}
</script>
