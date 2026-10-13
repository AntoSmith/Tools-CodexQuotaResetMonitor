const assert=require('node:assert/strict');
const {main}=require('../quota_monitor/browser_collect.cjs');
async function run({parentComplete=true,wrongParent=false,contextEnabled=true,feedStatus='ok',detailMissing=false,detailContextOnly=false,provider='reset_feed',extraOptions={}}={}){
 const visits=[];let url;
 const own={id:'20',username:'thsottiaux',text:'OK fine. Coming Tuesday',created_at:'2026-09-22T00:00:00Z',complete:true,context:[]};
 const parent={id:wrongParent?'9':'10',username:'someone',text:'You owe us a banked reset',complete:parentComplete};
 if(detailContextOnly)own.context=[{...parent,relation:'preceding_in_thread',evidence:'detail_order'}];
 const page={setDefaultTimeout(){},on(){},mainFrame(){return this;},
  async goto(u){url=u;visits.push(u);},async waitForSelector(){},async waitForTimeout(){},
  async evaluate(fn){return {url,login_prompt:true,posts:url.includes('/someone/')?[parent]:detailMissing?[]:[own]};}};
 const context={async route(){},pages(){return [page];},async close(){}};
 const report=await main({accounts:[{username:'thsottiaux'}],max_posts:1,max_scrolls:0,context_enabled:contextEnabled,
  [provider]:{status:feedStatus,fetched_at:'2026-09-22T00:01:00Z',source:provider==='divin_feed'?'https://divin.cc/':'https://codex-reset.com/',candidates:[{id:'20',username:'thsottiaux',url:'https://x.com/thsottiaux/status/20',...(detailContextOnly?{}:{parent_id:'10',parent_username:'someone'})}]},...extraOptions},
  {async launchPersistentContext(){return context;}});
 return {report,visits};
}
(async()=>{
 const good=await run();
 assert.deepEqual(good.visits,['https://x.com/thsottiaux/status/20','https://x.com/someone/status/10']);
 assert.equal(good.report.posts[0].context[0].text,'You owe us a banked reset');
 assert.equal(good.report.posts[0].context[0].evidence,'codex_reset_parent_link');
 assert.equal(good.report.posts[0].discovery_source,'https://codex-reset.com/');
 const divin=await run({provider:'divin_feed'});
 assert.deepEqual(divin.visits,good.visits);
 assert.equal(divin.report.posts[0].discovery_source,'https://divin.cc/');
 assert.equal(divin.report.posts[0].context[0].evidence,'divin_context_link');
 const conflict=await run({extraOptions:{divin_feed:{status:'ok',candidates:[{id:'20',username:'thsottiaux',url:'https://x.com/thsottiaux/status/20',parent_id:'9',parent_username:'someone'}]}}});
 assert.deepEqual(conflict.visits,['https://x.com/thsottiaux/status/20'],'same ID shares budget and contradictory parent hints are dropped');
 assert.deepEqual(conflict.report.posts[0].context,[]);
 const foreign=await run({feedStatus:'stale',extraOptions:{divin_feed:{status:'ok',candidates:[{id:'20',username:'attacker',url:'https://x.com/attacker/status/20'}]}}});
 assert.equal(foreign.visits[0],'https://x.com/thsottiaux/with_replies','foreign accounts cannot become discovery seeds');
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
