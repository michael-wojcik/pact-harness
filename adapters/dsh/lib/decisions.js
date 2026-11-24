/**
 * Rendering directives into DSH decisions.
 *
 * The bridge never invents a verdict; it renders what the harness decided and
 * applies the configured enforcement policy. In shadow mode a deny from a gate
 * that is not on the enforce list becomes an allow plus a logged notice, so the
 * verdict is observable without interrupting work.
 */

/** Whether a refusal should actually refuse. */
export function shouldEnforce(rule, settings) {
  if (settings.mode === 'enforce') return true;
  if (settings.mode !== 'shadow') return false;
  return settings.enforceGates.includes(rule);
}

/** The first refusal in a directive list, if any. */
export function refusalOf(directives) {
  return directives.find((directive) => directive.kind === 'deny');
}

/** The first approval request in a directive list, if any. */
export function askOf(directives) {
  return directives.find((directive) => directive.kind === 'ask');
}

/** The first turn refusal in a directive list, if any. */
export function blockOf(directives) {
  return directives.find((directive) => directive.kind === 'block');
}

/** The first cancel in a directive list, if any. */
export function cancelOf(directives) {
  return directives.find((directive) => directive.kind === 'cancel');
}

/** Every context injection in a directive list. */
export function injectionsOf(directives) {
  return directives.filter((directive) => directive.kind === 'inject');
}

/** Every user-facing notice in a directive list. */
export function noticesOf(directives) {
  return directives.filter((directive) => directive.kind === 'notify');
}

/**
 * Decide a pre-dispatch outcome.
 *
 * Returns either a DSH PreToolDecision or null, where null means "call next()".
 * Shadowed refusals are reported through `shadowed` so the caller can log them.
 */
export function decidePre(directives, settings) {
  const refusal = refusalOf(directives);
  if (refusal) {
    if (shouldEnforce(refusal.rule, settings)) {
      return { decision: { kind: 'deny', reason: refusal.reason }, shadowed: null };
    }
    return {
      decision: null,
      shadowed: `PACT would have denied (${refusal.rule ?? 'unknown gate'}): ${refusal.reason}`,
    };
  }
  const ask = askOf(directives);
  if (ask) return { decision: { kind: 'ask', reason: ask.reason }, shadowed: null };
  const cancel = cancelOf(directives);
  if (cancel) return { decision: { kind: 'cancel' }, shadowed: null };
  return { decision: null, shadowed: null };
}

/** Build the text blocks a turn-end steering message carries. */
export function turnEndMessage(reason) {
  return reason;
}
