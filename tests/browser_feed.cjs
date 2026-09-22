const assert=require('node:assert/strict');
const {main}=require('../quota_monitor/browser_collect.cjs');
async function run({parentComplete=true,wrongParent=false,contextEnabled=true,feedStatus='ok',detailMissing=false,detailContextOnly=false}={}){
 const visits=[];let url;
 const own={id:'20',username:'thsottiaux',text:'OK fine. Coming Tuesday',created_at:'2026-09-22T00:00:00Z',complete:true,context:[]};
 const parent={id:wrongParent?'9':'10',username:'someone',text:'You owe us a banked reset',complete:parentComplete};
 if(detailContextOnly)own.context=[{...parent,relation:'preceding_in_thread',evidence:'detail_order'}];
 const page={setDefaultTimeout(){},on(){},mainFrame(){return this;},
  async goto(u){url=u;visits.push(u);},async waitForSelector(){},async waitForTimeout(){},
  async evaluate(fn){return {url,login_prompt:true,posts:url.includes('/someone/')?[parent]:detailMissing?[]:[own]};}};
 const context={async route(){},pages(){return [page];},async close(){}};
 const report=await main({accounts:[{username:'thsottiaux'}],max_posts:1,max_scrolls:0,context_enabled:contextEnabled,
  reset_feed:{status:feedStatus,fetched_at:'2026-09-22T00:01:00Z',source:'https://codex-reset.com/',candidates:[{id:'20',username:'thsottiaux',url:'https://x.com/thsottiaux/status/20',...(detailContextOnly?{}:{parent_id:'10',parent_username:'someone'})}]}},
  {async launchPersistentContext(){return context;}});
 return {report,visits};
}
(async()=>{
 const good=await run();
 assert.deepEqual(good.visits,['https://x.com/thsottiaux/status/20','https://x.com/someone/status/10']);
 assert.equal(good.report.posts[0].context[0].text,'You owe us a banked reset');
 assert.equal(good.report.posts[0].context[0].evidence,'codex_reset_parent_link');
 assert.equal(good.report.posts[0].discovery_source,'https://codex-reset.com/');
 for(const opts of [{parentComplete:false},{wrongParent:true}]){
  const r=await run(opts);assert.deepEqual(r.report.posts[0].context,[]);
  assert.equal(r.report.posts[0].context_status,'parent_unavailable');
 }
 const disabled=await run({contextEnabled:false});assert.equal(disabled.visits.length,1);assert.deepEqual(disabled.report.posts[0].context,[]);
 const fromPage=await run({detailContextOnly:true});assert.equal(fromPage.report.posts[0].context_status,'detail_context');assert.equal(fromPage.visits.length,1);
 const failed=await run({feedStatus:'stale'});assert.equal(failed.visits[0],'https://x.com/thsottiaux/with_replies');
 const noDetail=await run({detailMissing:true});assert.equal(noDetail.report.posts.length,0,'feed body is never substituted for unreadable X text');
 console.log('Browser feed tests passed (seed discovery, parent fallback, bounds, disabled/stale/missing)');
})().catch(e=>{console.error(e);process.exitCode=1;});
