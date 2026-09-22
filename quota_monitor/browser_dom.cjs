// DOM only: no application stores, cookies, network endpoints or embedded state.
function extract(username) {
  const absoluteUTC = (year, month, day, hour, minute) => {
    if (year < 1970 || year > 9999 || month < 0 || month > 11 || day < 1 || day > 31 || hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
    const date = new Date(Date.UTC(year, month, day, hour, minute));
    if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month || date.getUTCDate() !== day) return null;
    return date.toISOString();
  };
  const owned = (article, selector) => [...article.querySelectorAll(selector)]
    .filter(el => {
      if (el.closest('article') !== article) return false;
      for (let ancestor = el; ancestor && ancestor !== article; ancestor = ancestor.parentElement) {
        if (ancestor.matches('[data-testid="quoteTweet"], [data-testid="quote"], div[role="link"]')) return false;
      }
      return true;
    });
  const authoredText = node => {
    if (!node) return '';
    const translated = '.immersive-translate-target-wrapper, [data-immersive-translate-translation-element-mark]';
    if (node.matches(translated)) return '';
    if (!node.querySelector(translated)) return node.innerText;
    // Work on a detached copy: never change the user's rendered page. Keep
    // original Chinese as well as English; only known injected targets go away.
    const copy = node.cloneNode(true);
    copy.querySelectorAll(translated).forEach(el => el.remove());
    copy.querySelectorAll('br').forEach(el => el.replaceWith('\n'));
    return copy.textContent;
  };
  const parse = article => {
    if (owned(article, '[data-testid="socialContext"]').some(el => /reposted|retweeted|转帖|转推/i.test(el.innerText))) return null;
    if (owned(article, '[data-testid="promotedIndicator"], [data-testid="placementTracking"], [data-testid="ad"]')
      .length || owned(article, '[data-testid="socialContext"]').some(el => /promoted|advertisement|推广|广告/i.test(el.innerText))) return null;
    const links = owned(article, 'a[href]');
    const identities = links.map(a => {
      try {
        const url = new URL(a.getAttribute('href'), 'https://x.com');
        if (!['x.com', 'www.x.com', 'twitter.com'].includes(url.hostname)) return null;
        const m = url.pathname.match(/^\/([A-Za-z0-9_]{1,15})\/status\/(\d+)$/);
        return m ? {username:m[1], id:m[2], link:a} : null;
      } catch {return null;}
    }).filter(Boolean);
    // Unknown quote layout or multiple competing permalinks: fail closed.
    if (new Set(identities.map(x => `${x.username.toLowerCase()}/${x.id}`)).size !== 1) return null;
    const identity = identities[0];
    if (!identity) return null;
    const textNode = owned(article, '[data-testid="tweetText"]')[0]
      || owned(article, 'div[dir="auto"]').find(el => el.classList.contains('whitespace-pre-wrap'));
    const text = authoredText(textNode);
    if (!text.trim()) return null;
    const timeNode = identity.link.querySelector('time[datetime]');
    let stamp = timeNode?.getAttribute('datetime') || null;
    let timestamp_source = stamp ? 'time_datetime' : null;
    // Collector enforces UTC. A stored site/profile language can still affect
    // visible date formatting; accept only complete, unambiguous absolute dates.
    if (!stamp) {
      const display = identity.link.textContent.trim();
      const m = display.match(/^(\d{1,2}):(\d{2})\s*(AM|PM)\s*[·•]\s*([A-Z][a-z]{2})\s+(\d{1,2}),?\s+(\d{4})$/);
      if (m) {
        const month = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'].indexOf(m[4]);
        if (+m[1] >= 1 && +m[1] <= 12) stamp = absoluteUTC(+m[6], month, +m[5], +m[1] % 12 + (m[3] === 'PM' ? 12 : 0), +m[2]);
      } else {
        const chinese = display.match(/^(\d{1,2}):(\d{2})\s*[·•]\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日$/);
        if (chinese) stamp = absoluteUTC(+chinese[3], +chinese[4]-1, +chinese[5], +chinese[1], +chinese[2]);
      }
      if (stamp) timestamp_source = 'detail_display_utc_minute';
    }
    const folded = owned(article, 'button, a, [role="button"]').some(el =>
      /^(show more|read more|显示更多|展开)$/i.test(el.textContent.trim()));
    return {id:identity.id, username:identity.username, text,
      created_at:stamp, timestamp_source, complete:!folded};
  };
  // Keep every top-level position, including rejected articles: a rejected
  // neighbor is a boundary, never permission to reach farther back in a feed.
  const parsed = [...document.querySelectorAll('article')]
    .filter(article => !article.parentElement?.closest('article'))
    .map(article => ({post:parse(article), scope:article.closest('section')
      || article.closest('main, [role="main"]') || document.body}));
  const detail = location.pathname.match(/^\/([A-Za-z0-9_]{1,15})\/status\/(\d+)$/);
  const posts = [];
  for (let i = 0; i < parsed.length; i++) {
    const {post, scope} = parsed[i];
    if (!post || post.username.toLowerCase() !== username.toLowerCase()) continue;
    const context = [];
    if (detail && detail[1].toLowerCase() === username.toLowerCase() && detail[2] === post.id) {
      for (let j = i - 1; j >= 0 && context.length < 3; j--) {
        const preceding = parsed[j];
        const candidate = preceding.post;
        if (preceding.scope !== scope || !candidate || !candidate.complete
          || BigInt(candidate.id) >= BigInt(post.id)) break;
        context.unshift({...candidate, relation:'preceding_in_thread', evidence:'detail_order',
          url:`https://x.com/${candidate.username}/status/${candidate.id}`});
      }
    }
    posts.push({...post, context});
  }
  const body = document.body?.innerText || '';
  return {posts, url:location.href, login_prompt:/log in|sign up|登录|注册/i.test(body),
    challenge:/verify you are human|unusual activity|验证您是人类|异常活动/i.test(body)};
}
module.exports = {extract};
