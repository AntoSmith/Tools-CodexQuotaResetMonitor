// Interactive setup only. Credentials stay in the browser; no DOM or cookies
// are read, and no collection/outbox/notification code is called here.
const {chromium} = require('playwright');
const fs = require('node:fs');

async function login(options, browserType = chromium) {
  const context = await browserType.launchPersistentContext(options.profile, {
    channel:options.channel, headless:false, locale:'en-US', timezoneId:'UTC',
    viewport:{width:1280,height:900}, chromiumSandbox:true, timeout:20000,
  });
  const closed = new Promise(resolve => context.once('close', resolve));
  try {
    const page = context.pages()[0] || await context.newPage();
    try {
      await page.goto(`https://x.com/${options.username}/with_replies`, {waitUntil:'domcontentloaded',timeout:30000});
    } catch {
      // Navigation can time out or the user can close the window while loading.
      // Leave an open window under manual control; the close event still wins.
      process.stdout.write('页面未加载完成；可在浏览器内重试，或关窗退出。\n');
    }
    await closed;
  } finally { await context.close(); }
}
module.exports = {login};
if (require.main === module) login(JSON.parse(fs.readFileSync(0,'utf8')))
  .catch(()=>{process.stderr.write('登录浏览器启动失败；请停止监控并关闭占用同一配置目录的浏览器后重试。\n');process.exitCode=1;});
