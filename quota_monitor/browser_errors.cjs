// Report bounded categories, never arbitrary error messages, URLs or credentials.
function failure(err, stage, httpStatus = null) {
  const message = String(err.message || '');
  let code = message.match(/net::ERR_[A-Z_]+/)?.[0];
  if (!code && /Target page, context or browser has been closed|Target closed/i.test(message)) code = 'browser_or_page_closed';
  if (!code && /Execution context was destroyed/i.test(message)) code = 'navigation_interrupted';
  return {stage, code:code || (err.name === 'TimeoutError' ? 'TimeoutError' : 'Error'), http_status:httpStatus};
}
module.exports = {failure};
