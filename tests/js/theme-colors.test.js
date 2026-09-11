import { describe, it, expect } from 'vitest';
import { loadApp } from './load-app.js';

describe('Problem Files table uses design-system color tokens', () => {
  it('renders status colors as var(--...) tokens, not hardcoded hex', async () => {
    const window = await loadApp();
    const { document } = window;

    window.showOpsResult(
      {
        checked: 2, clean: 0, repaired: 1, quarantined: 0,
        permission_errors: 0, skipped: 0, errors: 0,
        problems: [
          { status: 'repaired', corruption_type: 'truncated', path: '/a.jpg', error: '' },
          { status: 'permission_error', corruption_type: 'unknown', path: '/b.jpg', error: 'denied' },
        ],
      },
      'repair'
    );

    const html = document.getElementById('opsResultGrid').innerHTML;
    expect(html).toContain('var(--success)');
    expect(html).toContain('var(--danger)');
    expect(html).toContain('var(--text-muted)');
    // The old hardcoded hex values this replaced must not reappear.
    expect(html).not.toMatch(/#4caf50|#ff9800|#f44336|#aaa\b|#888\b/);
  });
});
