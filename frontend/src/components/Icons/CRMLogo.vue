<template>
  <!-- Brand-mark alias. Every call site imports CRMLogo, so this file is the
       single place to swap the app-wide mark — point it at another icon
       component and the sidebar, the About modal, the onboarding card and the
       theme switcher all follow.
       See demo-package/specs/branding-notes.md. -->
  <ParamaMark />
</template>

<script setup>
import ParamaMark from '@/components/Icons/ParamaMark.vue'
</script>
