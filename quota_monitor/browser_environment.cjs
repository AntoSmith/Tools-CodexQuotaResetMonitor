// CDP's Intl locale override alone does not change navigator.language or HTTP
// Accept-Language. A saved Windows profile may otherwise keep rendering X in Chinese.
async function configureBackgroundPage(context, page) {
  await page.setViewportSize({width:1280, height:900});
  const session = await context.newCDPSession(page);
  const userAgent = await page.evaluate(() => navigator.userAgent);
  await session.send('Network.setUserAgentOverride', {userAgent, acceptLanguage:'en-US,en'});
  await session.send('Emulation.setTimezoneOverride', {timezoneId:'UTC'});
  await session.send('Emulation.setLocaleOverride', {locale:'en-US'});
  // Keep the session attached for the entire page lifetime so overrides persist.
}
module.exports = {configureBackgroundPage};
