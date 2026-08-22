/**
 * WCAG contrast check for the console's OKLCH token pairs.
 *
 * The design system defines colour in OKLCH, which is perceptually uniform but says nothing
 * about contrast ratios. This converts each token to sRGB, composites any alpha over its
 * backdrop the way the browser does, and reports the ratio so the pairs are verified rather
 * than assumed.
 *
 * Three classes of check:
 *   TEXT  4.5:1  every foreground level against every surface it can land on (1.4.3)
 *   OBJECT 3.0:1 control boundaries, meter fills, focus rings (1.4.11)
 *   STEP   informational — surface separations that carry elevation and hover. Not
 *          WCAG-regulated, but a step under ~1.06 is imperceptible and reads as a bug.
 *
 * Run: node scripts/check-contrast.mjs
 * Exits non-zero if any TEXT or OBJECT pair fails.
 */

// ---------------------------------------------------------------- colour maths

/** OKLCH -> linear sRGB. Coefficients from the Oklab specification. */
function oklchToLinearSrgb(L, C, hDeg) {
  const h = (hDeg * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);

  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;

  const l = l_ * l_ * l_;
  const m = m_ * m_ * m_;
  const s = s_ * s_ * s_;

  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

const clamp01 = (v) => Math.min(1, Math.max(0, v));

const encode = (v) => {
  const c = clamp01(v);
  return c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
};

const decode = (v) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));

/** `oklch(L C H / A)` -> gamma sRGB channels plus alpha. */
function parse(token) {
  if (typeof token === "object" && token.__rgb) return { rgb: token.__rgb, alpha: 1 };
  const match =
    /^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:\/\s*([\d.]+)\s*)?\)$/.exec(token.trim());
  if (!match) throw new Error(`unparseable colour: ${token}`);
  const [, L, C, H, A] = match;
  return {
    rgb: oklchToLinearSrgb(Number(L), Number(C), Number(H)).map(encode),
    alpha: A === undefined ? 1 : Number(A),
  };
}

/** Composite a possibly-translucent foreground over an opaque backdrop, in gamma space. */
function over(fg, bg) {
  return fg.rgb.map((c, i) => c * fg.alpha + bg.rgb[i] * (1 - fg.alpha));
}

function luminance(gammaRgb) {
  const [r, g, b] = gammaRgb.map(decode);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Resolve a token against a backdrop, flattening alpha. */
function resolve(token, backdrop) {
  const parsed = parse(token);
  if (parsed.alpha === 1) return parsed.rgb;
  return over(parsed, { rgb: parse(backdrop).rgb, alpha: 1 });
}

/** Pre-composite a translucent token so it can serve as an opaque backdrop. */
const on = (token, backdrop) => ({ __rgb: resolve(token, backdrop) });

// ---------------------------------------------------------------- tokens
// Mirrors src/app/globals.css. A divergence here is a real defect, so values are written
// out rather than parsed loosely.

const T = {
  ink100: "oklch(0.972 0.003 265)",
  ink150: "oklch(0.955 0.004 265)",
  ink175: "oklch(0.955 0.005 265)",
  ink200: "oklch(0.938 0.006 265)",
  ink300: "oklch(0.86 0.007 265)",
  ink350: "oklch(0.775 0.01 265)",
  ink450: "oklch(0.66 0.011 265)",
  ink475: "oklch(0.635 0.012 265)",
  ink500: "oklch(0.605 0.012 265)",
  ink600: "oklch(0.515 0.011 265)",
  ink650: "oklch(0.465 0.012 265)",
  ink700: "oklch(0.42 0.013 265)",
  ink800: "oklch(0.3 0.01 265)",
  ink825: "oklch(0.26 0.009 265)",
  ink850: "oklch(0.235 0.008 265)",
  ink900: "oklch(0.212 0.007 265)",
  ink950: "oklch(0.163 0.006 265)",
  ink1000: "oklch(0.115 0.005 265)",

  canvasL: "oklch(0.975 0.006 82)",
  surfaceL: "oklch(0.996 0.002 82)",
  raisedL: "oklch(0.998 0.001 82)",
  hoverL: "oklch(0.948 0.008 255)",

  accent300: "oklch(0.788 0.096 255)",
  accent400: "oklch(0.652 0.148 255)",
  accent500: "oklch(0.576 0.171 255)",
  accent600: "oklch(0.512 0.163 255)",
  accent700: "oklch(0.444 0.139 255)",

  pass700: "oklch(0.432 0.104 152)",
  pass300: "oklch(0.72 0.13 152)",
  warn700: "oklch(0.498 0.108 74)",
  warn300: "oklch(0.79 0.13 74)",
  fail700: "oklch(0.462 0.172 25)",
  fail300: "oklch(0.7 0.16 25)",

  // Translucent fills, as written in globals.css
  passQuietL: "oklch(0.548 0.128 152 / 0.12)",
  warnQuietL: "oklch(0.638 0.132 74 / 0.15)",
  failQuietL: "oklch(0.554 0.196 25 / 0.10)",
  accentQuietL: "oklch(0.576 0.171 255 / 0.10)",
  passQuietD: "oklch(0.72 0.13 152 / 0.16)",
  warnQuietD: "oklch(0.79 0.13 74 / 0.16)",
  failQuietD: "oklch(0.7 0.16 25 / 0.18)",
  accentQuietD: "oklch(0.652 0.148 255 / 0.16)",

  hairlineStrongL: "oklch(0.163 0.006 265 / 0.20)",
  hairlineStrongD: "oklch(1 0 0 / 0.20)",
};

/*
 * Semantic aliases, named exactly as globals.css names them.
 *
 * The ramp entries above are steps; these are roles. Checking a role against a bare step is how the
 * `--border-control` drift survived — globals.css moved the role onto ink-450 and this file went on
 * verifying ink-500, which passes. Anything held to a WCAG threshold is declared here so the two
 * files can be compared by name.
 */
T.borderControlL = T.ink500;
T.borderControlD = T.ink600;

const TEXT = 4.5;
const OBJECT = 3.0;
const STEP = 1.06;

// [theme, kind, label, foreground, backdrop, required]
const checks = [
  // ================================================================ LIGHT — text
  ["light", "TEXT", "fg on canvas", T.ink950, T.canvasL, TEXT],
  ["light", "TEXT", "fg on surface", T.ink950, T.surfaceL, TEXT],
  ["light", "TEXT", "fg-secondary on canvas", T.ink700, T.canvasL, TEXT],
  ["light", "TEXT", "fg-secondary on surface", T.ink700, T.surfaceL, TEXT],
  ["light", "TEXT", "fg-secondary on sunken", T.ink700, T.ink175, TEXT],
  ["light", "TEXT", "fg-secondary on inset (pill-quiet)", T.ink700, T.ink200, TEXT],
  ["light", "TEXT", "fg-tertiary on canvas", T.ink650, T.canvasL, TEXT],
  ["light", "TEXT", "fg-tertiary on surface", T.ink650, T.surfaceL, TEXT],
  ["light", "TEXT", "fg-tertiary on sunken", T.ink650, T.ink175, TEXT],
  ["light", "TEXT", "fg-tertiary on inset", T.ink650, T.ink200, TEXT],
  ["light", "TEXT", "fg-quiet (overline) on canvas", T.ink600, T.canvasL, TEXT],
  ["light", "TEXT", "fg-quiet on surface", T.ink600, T.surfaceL, TEXT],
  ["light", "TEXT", "fg-quiet on raised", T.ink600, T.raisedL, TEXT],
  ["light", "TEXT", "fg-quiet on sunken", T.ink600, T.ink175, TEXT],
  ["light", "TEXT", "fg-quiet on inset", T.ink600, T.ink200, TEXT],
  ["light", "TEXT", "accent link on canvas", T.accent600, T.canvasL, TEXT],
  ["light", "TEXT", "accent link on surface", T.accent600, T.surfaceL, TEXT],
  ["light", "TEXT", "on-accent on primary button", T.raisedL, T.accent600, TEXT],
  ["light", "TEXT", "on-accent on primary hover", T.raisedL, T.accent700, TEXT],
  ["light", "TEXT", "accent on accent-quiet pill", T.accent600, on(T.accentQuietL, T.surfaceL), TEXT],
  ["light", "TEXT", "pass on pass-quiet pill", T.pass700, on(T.passQuietL, T.surfaceL), TEXT],
  ["light", "TEXT", "warn on warn-quiet pill", T.warn700, on(T.warnQuietL, T.surfaceL), TEXT],
  ["light", "TEXT", "fail on fail-quiet pill", T.fail700, on(T.failQuietL, T.surfaceL), TEXT],
  ["light", "TEXT", "fail on danger button", T.fail700, on(T.failQuietL, T.raisedL), TEXT],
  ["light", "TEXT", "page-line on document canvas", T.ink650, T.raisedL, TEXT],

  // ================================================================ LIGHT — objects
  //
  // `borderControlL`, not a bare ink step. These three read `T.ink500` while globals.css defined
  // `--border-control` as ink-450, so the gate was verifying a colour the stylesheet did not use —
  // and the one it did use failed against the canvas at 2.73:1. Naming the alias is the fix that
  // makes the next such drift visible: a reader comparing the two files now compares like with
  // like instead of having to notice a digit.
  ["light", "OBJECT", "control border vs button face", T.borderControlL, T.raisedL, OBJECT],
  ["light", "OBJECT", "control border vs canvas", T.borderControlL, T.canvasL, OBJECT],
  ["light", "OBJECT", "control border vs surface", T.borderControlL, T.surfaceL, OBJECT],
  ["light", "OBJECT", "meter fill (accent) vs track", T.accent600, T.ink300, OBJECT],
  ["light", "OBJECT", "meter fill (accent) vs surface", T.accent600, T.surfaceL, OBJECT],
  // The risk dial's budget slider puts a meter directly on the page canvas rather than inside a
  // panel, which is a darker backdrop in light mode than any surface — so the fill is checked
  // against it too.
  ["light", "OBJECT", "meter fill (accent) vs canvas", T.accent600, T.canvasL, OBJECT],
  ["light", "OBJECT", "meter fill (pass) vs track", T.pass700, T.ink300, OBJECT],
  ["light", "OBJECT", "meter fill (warn) vs track", T.warn700, T.ink300, OBJECT],
  ["light", "OBJECT", "meter fill (fail) vs track", T.fail700, T.ink300, OBJECT],
  ["light", "OBJECT", "meter threshold tick vs track", T.ink700, T.ink300, OBJECT],
  // The threshold tick now carries a one-pixel surface-coloured halo so it stays visible where the
  // fill has passed it. Both edges of that sandwich are graphical objects conveying the threshold.
  ["light", "OBJECT", "meter threshold tick vs accent fill", T.surfaceL, T.accent600, OBJECT],
  ["light", "OBJECT", "meter threshold tick vs pass fill", T.surfaceL, T.pass700, OBJECT],
  ["light", "OBJECT", "meter threshold tick vs warn fill", T.surfaceL, T.warn700, OBJECT],
  // Slider thumb: an accent disc ringed in the raised surface, sitting on the page canvas.
  ["light", "OBJECT", "slider thumb vs canvas", T.accent600, T.canvasL, OBJECT],
  ["light", "OBJECT", "slider thumb pressed vs canvas", T.accent700, T.canvasL, OBJECT],
  // Empty-state mark: the dashed border is the only thing separating "not measured" from "nothing to
  // show", so it conveys information and is held to 3:1 rather than to the decorative hairline.
  ["light", "OBJECT", "empty-state dashed mark vs sunken", T.ink600, T.ink175, OBJECT],
  ["light", "OBJECT", "focus ring vs canvas", T.accent500, T.canvasL, OBJECT],
  ["light", "OBJECT", "focus ring vs surface", T.accent500, T.surfaceL, OBJECT],
  ["light", "OBJECT", "focus ring vs raised", T.accent500, T.raisedL, OBJECT],
  ["light", "OBJECT", "nav active indicator vs canvas", T.accent600, T.canvasL, OBJECT],

  // ================================================================ LIGHT — steps
  ["light", "STEP", "canvas -> surface (elevation)", T.surfaceL, T.canvasL, STEP],
  ["light", "STEP", "surface -> hover", T.hoverL, T.surfaceL, STEP],
  ["light", "STEP", "canvas -> sunken", T.ink175, T.canvasL, 1.015],
  ["light", "STEP", "surface -> track", T.ink300, T.surfaceL, 1.35],
  ["light", "STEP", "document border vs surface", on(T.hairlineStrongL, T.raisedL), T.surfaceL, 1.15],

  // ================================================================ DARK — text
  ["dark", "TEXT", "fg on canvas", T.ink100, T.ink1000, TEXT],
  ["dark", "TEXT", "fg on surface", T.ink100, T.ink950, TEXT],
  ["dark", "TEXT", "fg-secondary on canvas", T.ink350, T.ink1000, TEXT],
  ["dark", "TEXT", "fg-secondary on surface", T.ink350, T.ink950, TEXT],
  ["dark", "TEXT", "fg-secondary on raised", T.ink350, T.ink900, TEXT],
  ["dark", "TEXT", "fg-secondary on inset (pill-quiet)", T.ink350, T.ink825, TEXT],
  ["dark", "TEXT", "fg-tertiary on canvas", T.ink450, T.ink1000, TEXT],
  ["dark", "TEXT", "fg-tertiary on surface", T.ink450, T.ink950, TEXT],
  ["dark", "TEXT", "fg-tertiary on raised", T.ink450, T.ink900, TEXT],
  ["dark", "TEXT", "fg-tertiary on inset", T.ink450, T.ink825, TEXT],
  ["dark", "TEXT", "fg-quiet (overline) on canvas", T.ink475, T.ink1000, TEXT],
  ["dark", "TEXT", "fg-quiet on surface", T.ink475, T.ink950, TEXT],
  ["dark", "TEXT", "fg-quiet on raised", T.ink475, T.ink900, TEXT],
  ["dark", "TEXT", "fg-quiet on sunken", T.ink475, T.ink850, TEXT],
  ["dark", "TEXT", "fg-quiet on inset", T.ink475, T.ink825, TEXT],
  ["dark", "TEXT", "accent link on surface", T.accent400, T.ink950, TEXT],
  ["dark", "TEXT", "accent link on raised", T.accent400, T.ink900, TEXT],
  ["dark", "TEXT", "on-accent on primary button", T.ink1000, T.accent400, TEXT],
  ["dark", "TEXT", "on-accent on primary hover", T.ink1000, T.accent300, TEXT],
  ["dark", "TEXT", "accent on accent-quiet pill", T.accent400, on(T.accentQuietD, T.ink950), TEXT],
  ["dark", "TEXT", "pass on pass-quiet pill", T.pass300, on(T.passQuietD, T.ink950), TEXT],
  ["dark", "TEXT", "warn on warn-quiet pill", T.warn300, on(T.warnQuietD, T.ink950), TEXT],
  ["dark", "TEXT", "fail on fail-quiet pill", T.fail300, on(T.failQuietD, T.ink950), TEXT],
  ["dark", "TEXT", "page-line on document canvas", T.ink450, T.ink900, TEXT],

  // ================================================================ DARK — objects
  ["dark", "OBJECT", "control border vs button face", T.borderControlD, T.ink900, OBJECT],
  ["dark", "OBJECT", "control border vs canvas", T.borderControlD, T.ink1000, OBJECT],
  ["dark", "OBJECT", "control border vs surface", T.borderControlD, T.ink950, OBJECT],
  ["dark", "OBJECT", "meter fill (accent) vs track", T.accent400, T.ink800, OBJECT],
  ["dark", "OBJECT", "meter fill (accent) vs surface", T.accent400, T.ink950, OBJECT],
  ["dark", "OBJECT", "meter fill (accent) vs canvas", T.accent400, T.ink1000, OBJECT],
  ["dark", "OBJECT", "meter fill (pass) vs track", T.pass300, T.ink800, OBJECT],
  ["dark", "OBJECT", "meter fill (warn) vs track", T.warn300, T.ink800, OBJECT],
  ["dark", "OBJECT", "meter fill (fail) vs track", T.fail300, T.ink800, OBJECT],
  ["dark", "OBJECT", "meter threshold tick vs track", T.ink350, T.ink800, OBJECT],
  ["dark", "OBJECT", "meter threshold tick vs accent fill", T.ink950, T.accent400, OBJECT],
  ["dark", "OBJECT", "meter threshold tick vs pass fill", T.ink950, T.pass300, OBJECT],
  ["dark", "OBJECT", "meter threshold tick vs warn fill", T.ink950, T.warn300, OBJECT],
  ["dark", "OBJECT", "slider thumb vs canvas", T.accent400, T.ink1000, OBJECT],
  ["dark", "OBJECT", "slider thumb pressed vs canvas", T.accent300, T.ink1000, OBJECT],
  ["dark", "OBJECT", "empty-state dashed mark vs sunken", T.ink475, T.ink850, OBJECT],
  ["dark", "OBJECT", "focus ring vs canvas", T.accent400, T.ink1000, OBJECT],
  ["dark", "OBJECT", "focus ring vs surface", T.accent400, T.ink950, OBJECT],
  ["dark", "OBJECT", "focus ring vs raised", T.accent400, T.ink900, OBJECT],
  ["dark", "OBJECT", "nav active indicator vs canvas", T.accent400, T.ink1000, OBJECT],

  // ================================================================ DARK — steps
  ["dark", "STEP", "canvas -> surface (elevation)", T.ink950, T.ink1000, 1.05],
  ["dark", "STEP", "surface -> raised (elevation)", T.ink900, T.ink950, 1.05],
  ["dark", "STEP", "surface -> hover", T.ink825, T.ink950, STEP],
  ["dark", "STEP", "surface -> track", T.ink800, T.ink950, 1.35],
];

let hardFailures = 0;
let softFailures = 0;
let group = "";

for (const [theme, kind, label, fg, bg, required] of checks) {
  const key = `${theme} ${kind}`;
  if (key !== group) {
    group = key;
    console.log(`\n${theme.toUpperCase()} — ${kind}`);
    console.log("-".repeat(72));
  }

  const ratio = contrast(resolve(fg, bg), parse(bg).rgb);
  const ok = ratio >= required;
  if (!ok) {
    if (kind === "STEP") softFailures += 1;
    else hardFailures += 1;
  }

  const mark = ok ? "pass" : kind === "STEP" ? "soft" : "FAIL";
  console.log(
    `${mark}  ${ratio.toFixed(2).padStart(6)}:1  (needs ${required.toFixed(2)})  ${label}`,
  );
}

console.log("");
console.log(
  `${checks.length} checks: ${checks.filter((c) => c[1] === "TEXT").length} text, ` +
    `${checks.filter((c) => c[1] === "OBJECT").length} object, ` +
    `${checks.filter((c) => c[1] === "STEP").length} step`,
);

if (softFailures > 0) {
  console.log(`${softFailures} perceptual step(s) below the advisory floor.`);
}
if (hardFailures > 0) {
  console.error(`\n${hardFailures} WCAG check(s) FAILED.`);
  process.exit(1);
}
console.log("All WCAG text and object checks passed.");
