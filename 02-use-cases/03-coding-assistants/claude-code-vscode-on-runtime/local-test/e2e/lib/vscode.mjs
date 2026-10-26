// Drives the VS Code web workbench through its UI the way a person would: the command palette (F1),
// quick open, the integrated terminal and the editor. Chrome runs with --disable-3d-apis, so xterm.js
// falls back to its DOM renderer and terminal text can be read from .xterm-rows.

export async function waitForWorkbench(page, timeout) {
  await page.waitForSelector('.monaco-workbench', { timeout });
}

// The visible terminal's rows (VS Code keeps other terminals in the DOM, hidden). Runs in the page.
const VISIBLE_ROWS = `[...document.querySelectorAll('.terminal-wrapper .xterm-rows')].find((e) => e.checkVisibility())`;

// Text of the visible terminal viewport, with non-breaking spaces normalised.
export function terminalText(page) {
  return page.evaluate(`(${VISIBLE_ROWS}?.innerText ?? '').replace(/\\u00a0/g, ' ')`);
}

export async function waitForTerminalText(page, pattern, timeout = 30000) {
  const source = pattern instanceof RegExp ? pattern.source : pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const flags = pattern instanceof RegExp ? pattern.flags.replace('g', '') : '';
  try {
    await page.waitForFunction(`new RegExp(${JSON.stringify(source)}, ${JSON.stringify(flags)}).test((${VISIBLE_ROWS}?.innerText ?? '').replace(/\\u00a0/g, ' '))`, { timeout, polling: 250 });
  } catch (err) {
    const seen = await terminalText(page).catch(() => '(unreadable)');
    throw new Error(`terminal never showed ${pattern} within ${timeout} ms. Terminal viewport:\n${seen}`, { cause: err });
  }
}

async function quickInputOpen(page) {
  await page.waitForSelector('.quick-input-widget:not([style*="display: none"]) .quick-input-box input', { visible: true, timeout: 10000 });
}

// Runs a command by its palette label, e.g. "Terminal: Create New Terminal".
export async function runCommand(page, label, { timeout = 15000 } = {}) {
  await page.keyboard.press('Escape');
  await page.keyboard.press('F1');
  await quickInputOpen(page);
  await page.keyboard.type(label);
  const row = await page.waitForFunction((want) => {
    const rows = [...document.querySelectorAll('.quick-input-widget .quick-input-list .monaco-list-row')];
    return rows.find((r) => (r.querySelector('.label-name')?.textContent ?? '').replace(/\u00a0/g, ' ').trim() === want) ?? null;
  }, { timeout, polling: 100 }, label).catch(async (err) => {
    const seen = await page.evaluate(() => [...document.querySelectorAll('.quick-input-widget .monaco-list-row .label-name')].map((e) => e.textContent).join(' | '));
    throw new Error(`command "${label}" not in the palette; saw: ${seen}`, { cause: err });
  });
  await row.asElement().click();
}

// Opens a workspace file through quick open (the palette without the ">" prefix).
export async function openFile(page, name, { timeout = 20000 } = {}) {
  await page.keyboard.press('Escape');
  await page.keyboard.press('F1');
  await quickInputOpen(page);
  await page.keyboard.press('Backspace');
  await page.keyboard.type(name);
  const row = await page.waitForFunction((want) => {
    const rows = [...document.querySelectorAll('.quick-input-widget .quick-input-list .monaco-list-row')];
    return rows.find((r) => (r.querySelector('.label-name')?.textContent ?? '').trim() === want) ?? null;
  }, { timeout, polling: 200 }, name);
  await row.asElement().click();
  await page.waitForFunction((want) => document.querySelector('.tab.active')?.getAttribute('aria-label')?.includes(want), { timeout }, name);
  await page.waitForSelector('.editor-instance .monaco-editor .view-lines', { visible: true, timeout });
}

// Puts the cursor in the active editor. The click goes to the lower part of the text area: in an empty
// file the middle of line 1 holds the "Generate code / select a language" hint, whose links take the click.
export async function focusEditor(page) {
  const inEditor = () => page.evaluate(() => Boolean(document.activeElement?.closest('.editor-instance .monaco-editor')));
  if (!(await inEditor())) {
    const area = await page.waitForSelector('.editor-instance .monaco-editor .overflow-guard', { visible: true, timeout: 10000 });
    const box = await area.boundingBox();
    await page.mouse.click(box.x + box.width * 0.4, box.y + box.height * 0.7);
  }
  await page.waitForFunction(() => document.activeElement?.closest('.editor-instance .monaco-editor'), { timeout: 10000 });
}

// The cursor position the status bar shows, e.g. "Ln 3, Col 1".
export function cursorPosition(page) {
  return page.evaluate(() => /Ln \d+, Col \d+/.exec(document.querySelector('.statusbar')?.innerText ?? '')?.[0] ?? null);
}

// Pastes text into the active editor and waits until the cursor sits at its end. A synthetic paste is now and
// then ignored (focus moved away between the click and the event), so it retries while the editor is still
// empty; a paste is one atomic edit, so a retry can't double the text.
export async function pasteIntoEditor(page, text, { attempts = 3, timeout = 15000 } = {}) {
  const want = `Ln ${text.split('\n').length}, Col ${text.length - text.lastIndexOf('\n')}`;
  for (let attempt = 1; ; attempt += 1) {
    await focusEditor(page);
    await pasteText(page, text);
    try {
      await page.waitForFunction((w) => (document.querySelector('.statusbar')?.innerText ?? '').includes(w), { timeout, polling: 250 }, want);
      return attempt;
    } catch (err) {
      const at = await cursorPosition(page);
      if (at !== 'Ln 1, Col 1' || attempt >= attempts) {
        throw new Error(`pasted ${text.length} characters ${attempt} time(s), but the cursor is at ${at}, not ${want}`, { cause: err });
      }
    }
  }
}

// The e2e's own terminal: renamed "e2e" and prompting "e2e$ ", so it can be told apart from the box's
// folder-open "Claude Code" task terminal, which grabs the panel (reveal always, focus) when it starts.
const MY_TITLE = 'e2e';
const MY_PROMPT = /^e2e\$/m;
const visibleScreen = `[...document.querySelectorAll('.terminal-wrapper .xterm-screen')].find((e) => e.checkVisibility()) ?? null`;
const MINE_IN_FRONT = `/${MY_PROMPT.source}/m.test(${VISIBLE_ROWS}?.innerText ?? '')`;

// Resolves true once a terminal titled `title` exists (the folder-open task), false after `timeout`.
export async function waitForTaskTerminal(page, title, timeout) {
  return page.waitForFunction((t) => [...document.querySelectorAll('.tabs-list .monaco-list-row')].some((r) => (r.getAttribute('aria-label') ?? '').endsWith(` ${t}`))
    || [...document.querySelectorAll('.terminal-wrapper .xterm-rows')].some((e) => /claude/i.test(e.textContent ?? '')), { timeout, polling: 250 }, title)
    .then(() => true, () => false);
}

// Claude Code's first screens in a new home, in the order it shows them, and its prompt once past them.
// Only the prompt is usable: nobody in a demo should have to answer a first-run question.
export const CLAUDE_SCREENS = {
  'the first-run theme picker': /Choose the text style that looks best with your terminal/,
  'the folder trust prompt': /Quick safety check: Is this a project you created or one you trust/,
  prompt: /\? for shortcuts/,
};

// Waits until the visible terminal shows one of CLAUDE_SCREENS (only Claude Code draws them).
// Resolves { screen, text }: the name of the screen and the terminal viewport.
export async function claudeScreen(page, timeout) {
  const sources = Object.values(CLAUDE_SCREENS).map((r) => r.source);
  try {
    await page.waitForFunction(`(() => {
      const t = (${VISIBLE_ROWS}?.innerText ?? '').replace(/\\u00a0/g, ' ');
      return ${JSON.stringify(sources)}.some((s) => new RegExp(s).test(t));
    })()`, { timeout, polling: 250 });
  } catch (err) {
    throw new Error(`Claude Code's terminal never showed its prompt or a first-run screen within ${timeout} ms. Terminal viewport:\n${await terminalText(page).catch(() => '(unreadable)')}`, { cause: err });
  }
  const text = await terminalText(page);
  return { screen: Object.entries(CLAUDE_SCREENS).find(([, r]) => r.test(text))?.[0] ?? '(it changed while being read)', text };
}

// VS Code's own AI chat on screen: a chat widget anywhere, a view in the Chat view container (the secondary
// side bar's default), or a "Chat" tab or title. Resolves a description of the first one found, or null.
// The Chat container itself may stay open and empty once its views are gone; that shows no chat.
export function visibleChat(page) {
  return page.evaluate(() => {
    const onScreen = (e) => e.checkVisibility() && e.getBoundingClientRect().width > 0;
    const text = (e) => e.innerText.replace(/\s+/g, ' ').trim().slice(0, 200);
    const widget = [...document.querySelectorAll('.interactive-session, .chat-widget')].find(onScreen);
    if (widget) return `a chat widget: ${text(widget)}`;
    const view = [...(document.getElementById('workbench.panel.chat')?.querySelectorAll('.pane') ?? [])].find(onScreen);
    if (view) return `a view in the Chat container: ${text(view)}`;
    const label = [...document.querySelectorAll('.part .composite-bar .action-label, .part .title-label')]
      .find((e) => onScreen(e) && /^chat\b/i.test((e.getAttribute('aria-label') || e.innerText).trim()));
    return label ? `a Chat tab or title: ${label.getAttribute('aria-label') || text(label)}` : null;
  });
}

// The loader's banners over the workbench (edge/web, not VS Code), e.g. the hint about signing in to AWS
// in the box: their text and position.
export function loaderBanners(page) {
  return page.evaluate(() => [...document.querySelectorAll('body > :not(.monaco-workbench) button')]
    .filter((b) => b.checkVisibility() && /^dismiss$/i.test(b.textContent.trim()))
    .map((b) => {
      const bar = b.parentElement;
      const r = bar.getBoundingClientRect();
      return { text: bar.innerText.replace(/\s+/g, ' ').replace(/\s*Dismiss$/i, '').trim(), rect: { x: r.x, y: r.y, width: r.width, height: r.height } };
    }));
}

// Clicks "Dismiss" on every loader banner, the way a person clears them before using the workbench.
export async function dismissLoaderBanners(page) {
  const clicked = await page.evaluate(() => {
    const buttons = [...document.querySelectorAll('body > :not(.monaco-workbench) button')]
      .filter((b) => b.checkVisibility() && /^dismiss$/i.test(b.textContent.trim()));
    for (const b of buttons) b.click();
    return buttons.length;
  });
  if (clicked) await page.waitForFunction(() => ![...document.querySelectorAll('body > :not(.monaco-workbench) button')]
    .some((b) => b.checkVisibility() && /^dismiss$/i.test(b.textContent.trim())), { timeout: 5000 });
  return clicked;
}

// Where the command palette (quick input) sits while open.
export async function paletteRect(page) {
  await page.keyboard.press('Escape');
  await page.keyboard.press('F1');
  await quickInputOpen(page);
  const rect = await page.evaluate(() => {
    const r = document.querySelector('.quick-input-widget').getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  });
  await page.keyboard.press('Escape');
  return rect;
}

export async function newTerminal(page) {
  const before = await page.evaluate(() => document.querySelectorAll('.terminal-wrapper').length);
  await runCommand(page, 'Terminal: Create New Terminal');
  await page.waitForFunction((n) => document.querySelectorAll('.terminal-wrapper').length > n, { timeout: 30000 }, before);
  // A prompt shows the shell is up.
  await page.waitForFunction(`/[$#] ?$/m.test((${VISIBLE_ROWS}?.innerText ?? '').replace(/\\u00a0/g, ' ').trimEnd())`, { timeout: 30000, polling: 250 });
  await runCommand(page, 'Terminal: Rename...');
  await quickInputOpen(page);
  await page.keyboard.type(MY_TITLE);
  await page.keyboard.press('Enter');
  await clickVisibleTerminal(page);
  await typeInTerminal(page, " export PS1='e2e$ '");
  await waitForTerminalText(page, MY_PROMPT, 10000);
}

async function clickVisibleTerminal(page) {
  const screen = (await page.waitForFunction(visibleScreen, { timeout: 10000 })).asElement();
  await screen.click();
  await page.waitForFunction(() => document.activeElement?.classList.contains('xterm-helper-textarea'), { timeout: 10000 });
}

export async function focusTerminal(page) {
  if (!(await page.evaluate(MINE_IN_FRONT))) {
    // Another terminal is in front (or the panel is closed): bring ours back through the tabs list.
    if (!(await page.evaluate(`!!(${visibleScreen})`))) await runCommand(page, 'Terminal: Focus Terminal');
    const row = await page.$(`.tabs-list .monaco-list-row[aria-label$=" ${MY_TITLE}"]`);
    if (row) await row.click();
    await page.waitForFunction(MINE_IN_FRONT, { timeout: 10000 })
      .catch(async (err) => { throw new Error(`could not bring the e2e terminal to the front; visible terminal:\n${await terminalText(page)}`, { cause: err }); });
  }
  // Clicking the terminal is more reliable than the palette's focus command, whose closing quick input
  // swallows the next keystroke.
  await clickVisibleTerminal(page);
}

// Types a shell command into the focused terminal and presses Enter.
export async function typeInTerminal(page, command) {
  await page.keyboard.type(command, { delay: 5 });
  await page.keyboard.press('Enter');
}

export async function shell(page, command, expect, timeout) {
  await focusTerminal(page);
  // The first keystroke after focusing is sometimes dropped; a leading space makes that harmless.
  await typeInTerminal(page, ' clear');
  // Wait for a really clear screen (just the prompt), so output of an earlier command can't match.
  await page.waitForFunction(`(() => { const t = (${VISIBLE_ROWS}?.innerText ?? '').replace(/\\u00a0/g, ' ').trim(); return !t.includes('\\n') && /[$#]$/.test(t); })()`, { timeout: 10000, polling: 100 })
    .catch(async (err) => { throw new Error(`the terminal did not clear:\n${await terminalText(page)}`, { cause: err }); });
  await typeInTerminal(page, command);
  if (expect) await waitForTerminalText(page, expect, timeout);
}

// Pastes text into the focused editor. Typing a megabyte stalls the editor; a paste is one edit.
export async function pasteText(page, text) {
  await page.evaluate((t) => {
    const dt = new DataTransfer();
    dt.setData('text/plain', t);
    document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  }, text);
}

// With a short auto-save delay (the web default, and the box's afterDelay) VS Code shows no dirty marker, so
// there is nothing reliable to wait for here: callers check the file itself.
export async function saveActiveEditor(page) {
  await runCommand(page, 'File: Save');
}

// Visible dialogs and notifications, to catch "cannot reconnect" and friends.
export function workbenchAlerts(page) {
  return page.evaluate(() => [...document.querySelectorAll('.monaco-dialog-box, .notification-toast, .notifications-center .notification-list-item')]
    .map((e) => e.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean));
}
