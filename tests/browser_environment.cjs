const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const {configureBackgroundPage} = require('../quota_monitor/browser_environment.cjs');
(async()=>{
 const browser = await chromium.launch({channel:'chrome',headless:true});
 try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const originalUA = await page.evaluate(()=>navigator.userAgent);
  // Simulate a saved profile's network/navigator language, without adding a
  // second Playwright Emulation override (which real CDP profiles don't have).
  const initial = await context.newCDPSession(page);
  await initial.send('Network.setUserAgentOverride',{userAgent:originalUA,acceptLanguage:'zh-CN,zh'});
  assert.equal(await page.evaluate(()=>navigator.language),'zh-CN');
  let acceptLanguage;
  await context.route('https://locale.test/**',async route=>{
   acceptLanguage=route.request().headers()['accept-language'];
   await route.fulfill({contentType:'text/html',body:'<html><body>Local test</body></html>'});
  });
  await configureBackgroundPage(context,page);
  await page.goto('https://locale.test/');
  const env=await page.evaluate(()=>({language:navigator.language,languages:navigator.languages,ua:navigator.userAgent,options:Intl.DateTimeFormat().resolvedOptions()}));
  assert.equal(env.language,'en-US');
  assert.equal(env.languages[0],'en-US');
  assert.deepEqual(env.languages,['en-US','en']);
  assert.equal(env.options.timeZone,'UTC');
  assert.equal(env.options.locale,'en-US');
  assert.equal(env.ua,originalUA);
  assert.match(acceptLanguage,/^en-US/);
  console.log('Background locale test passed: existing Chinese context -> English navigator/header and UTC, unchanged UA');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
