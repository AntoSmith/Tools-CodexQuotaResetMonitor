const assert = require('node:assert/strict');
const {main} = require('../quota_monitor/browser_collect.cjs');

async function run(overrides = {}) {
  const visits = []; let url = '', scrolls = 0;
  const reply = {id:'20', username:'demo', text:'OK fine. Coming Tuesday', complete:true,
    created_at:'2026-09-22T00:00:00Z', context:[{id:'10', text:'banked reset'}]};
  const page = {
    setDefaultTimeout(){}, on(){}, mainFrame(){return this;},
    async goto(value){url=value;visits.push(value);}, async waitForSelector(){},
    async waitForTimeout(){},
    async evaluate(fn) {
      if (fn.name !== 'extract') {scrolls++;return;}
      return {url, posts:url.endsWith('/with_replies') || url.includes('/status/') ? [reply] : [], login_prompt:true};
    },
  };
  const context = {async route(){}, pages(){return [page];}, async close(){}};
  const browser = {async launchPersistentContext(){return context;}};
  const result = await main({accounts:[{username:'demo'}], max_posts:5, max_scrolls:5,
    conversation_urls:['https://x.com/other/status/10'], ...overrides}, browser);
  return {result,visits,scrolls};
}

(async () => {
  const normal=await run();
  assert.equal(normal.visits[0],'https://x.com/demo/with_replies');
  assert.equal(normal.scrolls,1,'stop scrolling when no new IDs appear; discussion pages are not scrolled');
  assert.ok(normal.visits.includes('https://x.com/other/status/10'));
  assert.equal(normal.result.posts.length,1,'deduplicate across discovery pages');
  assert.equal(normal.result.posts[0].context.length,1);
  assert.equal(normal.result.pages_visited,normal.visits.length);
  assert.ok(normal.result.duration_seconds>=0);
  const disabled=await run({context_enabled:false});
  assert.deepEqual(disabled.result.posts[0].context,[]);
  assert.ok(!disabled.visits.includes('https://x.com/other/status/10'));
  const profile=await run({profile_only:true});
  assert.deepEqual(profile.visits,['https://x.com/demo']);
  const headOnly=await run({max_scrolls:0,conversation_urls:[]});
  assert.equal(headOnly.scrolls,0,'zero scrolls reads only the initially loaded head');
  console.log('browser_collect: passed');
})().catch(err=>{console.error(err);process.exitCode=1;});
