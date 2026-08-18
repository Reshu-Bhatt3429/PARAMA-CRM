<template>
  <!-- PARAMA wordmark — geometric sans built from stroked centerlines, so it
       needs no font and stays identical everywhere. `currentColor` lets it
       inherit the surrounding ink in both the light and the dark theme.
       Design source: src/images/parama-wordmark.svg -->
  <svg
    viewBox="0 0 488 112"
    fill="none"
    stroke="currentColor"
    stroke-width="12"
    stroke-linecap="round"
    stroke-linejoin="round"
    role="img"
    aria-label="PARAMA"
    xmlns="http://www.w3.org/2000/svg"
  >
    <g transform="translate(6 6)">
      <path d="M0 100 V0 H30 a26 26 0 0 1 0 52 H0" />
      <path transform="translate(80 0)" d="M0 100 L30 0 L60 100 M20 66 H40" />
      <path
        transform="translate(164 0)"
        d="M0 100 V0 H30 a26 26 0 0 1 0 52 H0 M28 52 L56 100"
      />
      <path transform="translate(244 0)" d="M0 100 L30 0 L60 100 M20 66 H40" />
      <path transform="translate(328 0)" d="M0 100 V0 L32 58 L64 0 V100" />
      <path transform="translate(416 0)" d="M0 100 L30 0 L60 100 M20 66 H40" />
    </g>
  </svg>
</template>
