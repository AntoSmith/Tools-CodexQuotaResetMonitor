const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const {extract} = require('../quota_monitor/browser_dom.cjs');
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  try {
    const page = await browser.newPage();
    await page.setContent(`<article><a href="/demo/status/2097043464538264003">8:00 AM · Sep 8, 2026</a>
      <div dir="auto" class="whitespace-pre-wrap">We will reset Codex usage.</div>
      <article><a href="/other/status/2097043464538264004">1h</a><div dir="auto" class="whitespace-pre-wrap">Quoted false claim</div></article></article>`);
    let r = await page.evaluate(extract, 'demo');
    assert.equal(r.posts.length, 1);
    assert.equal(r.posts[0].text, 'We will reset Codex usage.');
    assert.equal(r.posts[0].created_at, '2026-09-08T08:00:00.000Z');
    assert.equal(r.posts[0].complete, true);
    // Existing Windows profiles can keep zh-CN even when Intl locale was overridden.
    for (const [display, expected] of [
      ['5:20 · 2026年9月8日', '2026-09-08T05:20:00.000Z'],
      ['17:20 · 2026年9月8日', '2026-09-08T17:20:00.000Z'],
      ['0:00 · 2026年9月8日', '2026-09-08T00:00:00.000Z'],
      ['25:20 · 2026年9月8日', null],
      ['5:20 · 2026年2月31日', null],
      ['5:20 · 9月8日', null],
      ['2小时前', null],
    ]) {
      await page.setContent(`<article><a href="/demo/status/2097043464538264003">${display}</a><div data-testid="tweetText">Codex reset candidate</div></article>`);
      assert.equal((await page.evaluate(extract, 'demo')).posts[0].created_at, expected, display);
    }
    await page.setContent(`<article data-testid="tweet"><div data-testid="socialContext">Other reposted</div><a href="/demo/status/2097043464538264003"><time datetime="2026-09-08T08:00:00Z">1h</time></a><div data-testid="tweetText">Codex reset</div></article>`);
    assert.equal((await page.evaluate(extract, 'demo')).posts.length, 0);
    await page.setContent(`<article><a href="/demo/status/2097043464538264003">1h</a><div data-testid="tweetText">Codex will reset</div><button>Show more</button></article>`);
    r = await page.evaluate(extract, 'demo');
    assert.equal(r.posts[0].complete, false);
    assert.equal(r.posts[0].created_at, null);
    await page.setContent(`<article><a href="/other/status/2097043464538264004">1h</a><div data-testid="tweetText">Other author</div><article><a href="/demo/status/2097043464538264003">1h</a><div data-testid="tweetText">Nested quote</div></article></article>`);
    assert.equal((await page.evaluate(extract, 'demo')).posts.length, 0);
    await page.setContent(`<article><a href="/demo/status/2097043464538264003"><time datetime="2026-09-08T08:00:00Z">1h</time></a><div role="link" data-testid="quoteTweet"><a href="/other/status/2097043464538264004">Other</a><div data-testid="tweetText">We will reset Codex usage for everyone.</div></div></article>`);
    assert.equal((await page.evaluate(extract, 'demo')).posts.length, 0);
    // All detail-page fixtures are fulfilled locally; no X traffic is made.
    let fixture = '';
    await page.route('**/*', route => route.fulfill({contentType:'text/html; charset=utf-8', body:fixture}));
    const article = (author, id, text, extra = '') => `<article><a href="/${author}/status/${id}"><time datetime="2026-09-08T08:00:00Z">1h</time></a><div data-testid="tweetText">${text}</div>${extra}</article>`;
    const targetId = '2097043464538264003';
    const parentId = '2097043464538264002';
    const visit = async (path, html) => {
      fixture = html;
      await page.goto(`https://x.com${path}`);
      return page.evaluate(extract, 'demo');
    };
    // Browser translation extensions add text inside tweetText. It is not authored evidence.
    const translation = '<font class="notranslate immersive-translate-target-wrapper" lang="zh-CN"><br><font data-immersive-translate-translation-element-mark="1">Codex 额度已经重置</font></font>';
    r = await visit('/demo/with_replies', article('demo', targetId, `<span>Good choice, good choice${translation}</span><br><span>谢谢！</span>`));
    assert.equal(r.posts[0].text, 'Good choice, good choice\n谢谢！');
    assert.ok(await page.locator('.immersive-translate-target-wrapper').count(), 'extraction must not alter the live page');
    r = await visit(`/demo/status/${targetId}`, article('questioner', parentId, `Nice release.${translation}`) + article('demo', targetId, `Fine.${translation}`));
    assert.equal(r.posts[0].text, 'Fine.');
    assert.equal(r.posts[0].context[0].text, 'Nice release.');
    r = await visit('/demo/with_replies', article('demo', targetId, translation));
    assert.equal(r.posts.length, 0, 'translation-only content has no readable original');
    for (const [parent, reply] of [
      ['When will you reset Codex usage limits?', 'Fine, Tuesday.'],
      ['什么时候重置 Codex 使用额度？', '好，周二。'],
    ]) {
      r = await visit(`/demo/status/${targetId}`, `<main><section>${article('questioner', parentId, parent)}${article('demo', targetId, reply)}${article('commenter', '2097043464538264004', 'Codex resets Friday')}</section></main>`);
      assert.equal(r.posts.length, 1);
      assert.equal(r.posts[0].text, reply);
      assert.deepEqual(r.posts[0].context, [{id:parentId, username:'questioner', text:parent,
        created_at:'2026-09-08T08:00:00Z', timestamp_source:'time_datetime', complete:true,
        relation:'preceding_in_thread', evidence:'detail_order', url:`https://x.com/questioner/status/${parentId}`}]);
    }
    for (const path of ['/demo', '/demo/with_replies', `/other/status/${targetId}`, `/demo/status/${parentId}`, `/demo/status/${targetId}/photo/1`]) {
      r = await visit(path, article('questioner', parentId, 'Reset Codex?') + article('demo', targetId, 'Fine, Tuesday.'));
      assert.deepEqual(r.posts[0].context, [], path);
    }
    const quote = `<div role="link" data-testid="quoteTweet">${article('quoted', '2097043464538264001', 'Injected reset claim')}<div data-testid="tweetText">Quoted reset Friday</div></div>`;
    r = await visit(`/demo/status/${targetId}`, article('questioner', parentId, 'Reset Codex?', quote) + article('demo', targetId, 'Fine, Tuesday.', quote) + article('commenter', '2097043464538264004', 'Reset tomorrow'));
    assert.equal(r.posts[0].text, 'Fine, Tuesday.');
    assert.equal(r.posts[0].context[0].text, 'Reset Codex?');
    for (const barrier of [
      article('questioner', parentId, 'Reset Codex?', '<button>Show more</button>'),
      article('questioner', '2097043464538264004', 'Future ID'),
      article('questioner', 'not-a-status-id', 'Invalid ID'),
      article('questioner', parentId, 'Ad', '<span data-testid="promotedIndicator">Promoted</span>'),
      article('questioner', parentId, 'Repost', '<div data-testid="socialContext">Other reposted</div>'),
      '<article><div data-testid="tweetText">Unattributed reset claim</div></article>',
      article('questioner', parentId, '   '),
      article('questioner', parentId, 'Conflicting identity', `<a href="/other/status/${parentId}">Other</a>`),
    ]) {
      r = await visit(`/demo/status/${targetId}`, article('earlier', '2097043464538264000', 'Older reset claim') + barrier + article('demo', targetId, 'Fine, Tuesday.'));
      assert.deepEqual(r.posts[0].context, [], barrier);
    }
    r = await visit(`/demo/status/${targetId}`, `<aside>${article('sidebar', parentId, 'Reset Codex?')}</aside><main><section>${article('demo', targetId, 'Fine, Tuesday.')}</section></main>`);
    assert.deepEqual(r.posts[0].context, []);
    r = await visit(`/demo/status/${targetId}`, `<main><section>${article('sidebar', parentId, 'Reset Codex?')}</section><section>${article('demo', targetId, 'Fine, Tuesday.')}</section></main>`);
    assert.deepEqual(r.posts[0].context, []);
    r = await visit(`/demo/status/${targetId}`, article('earlier', '2097043464538264000', 'Do not cross') + '<article>Unknown article</article>' + article('questioner', parentId, 'Nearest reset?') + article('demo', targetId, 'Fine, Tuesday.'));
    assert.deepEqual(r.posts[0].context.map(x => x.text), ['Nearest reset?']);
    r = await visit(`/demo/status/${targetId}`, '<main><section>' + [0, 1, 2, 3].map(i => article('questioner', `209704346453826399${i}`, `Earlier ${i}`)).join('') + article('demo', targetId, 'Fine, Tuesday.') + '</section></main>');
    assert.deepEqual(r.posts[0].context.map(x => x.text), ['Earlier 1', 'Earlier 2', 'Earlier 3']);
    r = await visit(`/demo/status/${targetId}`, article('demo', targetId, 'Ad', '<span data-testid="promotedIndicator">Promoted</span>'));
    assert.equal(r.posts.length, 0);
    console.log('Browser DOM tests passed (timestamps, quote isolation, bounded detail context and barriers)');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
