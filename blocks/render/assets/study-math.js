/* A shared presentation rule; semantic variable subscripts are never changed. */
(function(root,factory){
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else root.DSHStudyMath=factory();
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  var operators=/\\(limsup|liminf|bigotimes|bigoplus|biguplus|bigsqcup|bigwedge|bigvee|bigcup|bigcap|coprod|iiint|iint|oint|sum|prod|lim|sup|inf|max|min|int)(?![A-Za-z])(?:\s*\\(?:nolimits|displaylimits|limits))?/g;
  function operatorLimits(source){
    var unicode={'∑':'sum','∏':'prod','∫':'int','∮':'oint','∬':'iint','∭':'iiint'};
    return String(source==null?'':source).replace(/[∑∏∫∮∬∭]/g,function(symbol){return '\\'+unicode[symbol]+' ';}).replace(operators,function(_,name){return '\\'+name+'\\limits';});
  }
  return {operatorLimits:operatorLimits};
});
