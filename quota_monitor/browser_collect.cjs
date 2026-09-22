const {chromium} = require('playwright');
const {extract} = require('./browser_dom.cjs');
const fs = require('node:fs');
const {failure} = require('./browser_errors.cjs');
const {configureBackgroundPage} = require('./browser_environment.cjs');

async function main(options, browserType = chromium) {
  const started = performance.now();
  const result = {source:'browser_dom', browser_mode:options.background ? 'background' : options.headed ? 'headed' : 'headless',
    collected_at:new Date().toISOString(), accounts:[], posts:[], pages_visited:0};
  let attached;
  if (options.background && !/^ws:\/\/127\.0\.0\.1:\d+\/devtools\/browser\/[A-Za-z0-9-]+$/.test(options.cdp_endpoint || '')) {
    throw new Error('Missing or invalid local background browser endpoint');
  }
  const context = options.background ? (attached = await browserType.connectOverCDP(options.cdp_endpoint, {timeout:20000})).contexts()[0] : await browserType.launchPersistentContext(options.profile, {
    channel:options.channel, headless:!options.headed, locale:'en-US', timezoneId:'UTC',
    viewport:{width:1280,height:900}, chromiumSandbox:true, timeout:20000,
  });
  try {
    // Images/video are not needed for text announcement monitoring.
    await context.route('**/*', route => ['image','media','font'].includes(route.request().resourceType()) ? route.abort() : route.continue());
    const page = context.pages()[0] || await context.newPage();
    if (options.background) {
      await configureBackgroundPage(context, page);
    }
    page.setDefaultTimeout(15000);
    let httpStatus = null;
    page.on('response', response => {
      if (response.request().isNavigationRequest() && response.frame() === page.mainFrame()) httpStatus = response.status();
    });
    async function visit(url, username) {
      result.pages_visited++;
      let stage = 'navigate';
      httpStatus = null;
      try {
        await page.goto(url, {waitUntil:'domcontentloaded', timeout:25000});
        stage = 'wait_for_posts';
        await page.waitForSelector('article', {timeout:10000}).catch(err => {
          if (err.name !== 'TimeoutError') throw err;
        });
        stage = 'extract';
        const snapshot = await page.evaluate(extract, username);
        return {...snapshot, http_status:httpStatus};
      } catch (err) {
        err.collectionFailure = failure(err, stage, httpStatus);
        throw err;
      }
    }
    for (const account of options.accounts) {
      const report = {username:account.username, coverage:'unavailable', pages:[], discovered:0, accepted:0, skipped:[], errors:[]};
      result.accounts.push(report);
      const discovered = new Map();
      let challenged = false;
      const contextEnabled = !options.profile_only && options.context_enabled !== false;
      const profile = `https://x.com/${account.username}`;
      const seeds = !options.profile_only && account.username.toLowerCase() === 'thsottiaux' && options.reset_feed?.status === 'ok'
        ? (options.reset_feed.candidates || []).filter(p => p.username === 'thsottiaux' && /^[0-9]{1,25}$/.test(p.id)
          && p.url === `https://x.com/thsottiaux/status/${p.id}`).slice(0,options.max_posts) : [];
      for (const seed of seeds) discovered.set(seed.id, seed);
      report.discovery_source = seeds.length ? 'https://codex-reset.com/' : 'x_public_pages';
      const urls = options.profile_only ? [profile] : [...(seeds.length ? [] : [profile + '/with_replies', profile]),
        ...(contextEnabled ? options.conversation_urls || [] : [])];
      const watched = new Set(contextEnabled ? options.conversation_urls || [] : []);
      const watchedIds = new Set();
      for (const url of new Set(urls)) {
        try {
          let snapshot = await visit(url, account.username);
          const pageReport = {url, final_url:snapshot.url, http_status:snapshot.http_status,
            visible_posts:snapshot.posts.length, login_prompt:snapshot.login_prompt,
            status:snapshot.posts.length ? 'partial' : 'empty_or_restricted'};
          report.pages.push(pageReport);
          if (snapshot.challenge) {report.errors.push('human_verification_required'); challenged = true; break;}
          const seenOnPage = new Set();
          for (let scroll = 0; scroll <= options.max_scrolls; scroll++) {
            const before = seenOnPage.size;
            for (const post of snapshot.posts) {
              discovered.set(post.id, post);
              seenOnPage.add(post.id);
              if (watched.has(url)) watchedIds.add(post.id);
            }
            // Discussion pages expose candidates only; load each author's own
            // detail URL below before associating any preceding conversation.
            if (!watched.has(url) && scroll < options.max_scrolls && seenOnPage.size > before) {
              await page.evaluate(() => window.scrollBy(0, 850));
              await page.waitForTimeout(1000);
              snapshot = await page.evaluate(extract, account.username);
            } else break;
          }
          pageReport.visible_posts = seenOnPage.size;
        } catch (err) {report.errors.push({page:url, ...(err.collectionFailure || failure(err, 'scroll', httpStatus))});}
      }
      report.discovered = discovered.size;
      if (challenged) continue;
      const sorted = [...discovered.values()].sort((a,b)=>BigInt(a.id)>BigInt(b.id)?-1:1);
      // Feed candidate order reserves space for recent reset text before casual
      // replies. Never use the provider's event labels to classify a post.
      const posts = [...seeds, ...sorted.filter(p=>!seeds.some(s=>s.id===p.id))];
      // Reserve at most half the detail budget for explicitly watched threads;
      // leave room for newer timeline posts so old pinned links cannot starve them.
      const pinned = posts.filter(p => watchedIds.has(p.id)).slice(0, Math.floor(options.max_posts / 2));
      const selected = [...pinned, ...posts.filter(p => !pinned.includes(p))].slice(0, options.max_posts);
      if (posts.length > options.max_posts) report.errors.push('detail_limit_reached');
      for (const candidate of selected) {
        try {
          const detail = await visit(`https://x.com/${account.username}/status/${candidate.id}`, account.username);
          if (detail.challenge) {report.errors.push('human_verification_required'); break;}
          const post = detail.posts.find(p => p.id === candidate.id);
          if (!post || !post.complete || !post.created_at) {
            report.skipped.push({id:candidate.id, reason:!post ? 'detail_unavailable' : !post.complete ? 'folded_text' : 'timestamp_unavailable'});
            continue;
          }
          const accepted = {...post, context:contextEnabled ? post.context || [] : []};
          const seed = seeds.find(s=>s.id===candidate.id);
          if (seed) {
            accepted.discovery_source = 'https://codex-reset.com/';
            accepted.feed_fetched_at = options.reset_feed.fetched_at;
            accepted.context_status = !contextEnabled ? 'disabled' : accepted.context.length ? 'detail_context' : 'not_available';
            if (contextEnabled && /^[0-9]{1,25}$/.test(seed.parent_id || '')
              && BigInt(seed.parent_id) < BigInt(post.id) && /^[A-Za-z0-9_]{1,15}$/.test(seed.parent_username || '')) {
              accepted.parent_id = seed.parent_id;
              accepted.parent_username = seed.parent_username;
              const matches = p=>p.id===seed.parent_id && p.username?.toLowerCase()===seed.parent_username.toLowerCase() && p.complete;
              if (accepted.context.some(matches)) accepted.context_status = 'parent_in_detail';
              else {
                accepted.context = [];
                accepted.context_status = 'parent_unavailable';
                try {
                  const parent = await visit(`https://x.com/${seed.parent_username}/status/${seed.parent_id}`, seed.parent_username);
                  if (parent.challenge) {report.errors.push('human_verification_required'); challenged = true;}
                  else {
                    const original = parent.posts.find(matches);
                    if (original) {
                      accepted.context = [{...original, relation:'parent_reported_by_feed',
                        evidence:'codex_reset_parent_link', reply_id:post.id,
                        url:`https://x.com/${seed.parent_username}/status/${seed.parent_id}`}];
                      accepted.context_status = 'parent_fetched';
                    }
                  }
                } catch (err) {report.errors.push({page:'parent',id:seed.parent_id,...(err.collectionFailure || failure(err,'parent',httpStatus))});}
              }
            }
          }
          result.posts.push(accepted);
          report.accepted++;
          report.coverage = 'partial_unverified';
          if (challenged) break;
        } catch (err) {report.skipped.push({id:candidate.id, ...(err.collectionFailure || failure(err, 'detail', httpStatus))});}
      }
    }
    result.duration_seconds = Math.round((performance.now() - started) / 10) / 100;
    return result;
  } finally {if (attached) await attached.close(); else await context.close();}
}

module.exports = {main};
if (require.main === module) main(JSON.parse(fs.readFileSync(0, 'utf8'))).then(result=>process.stdout.write(JSON.stringify(result)))
  .catch(err=>{process.stderr.write(`Browser runner failed: ${err.name}. Check Playwright, browser installation and profile lock.\n`);process.exitCode=1;});
