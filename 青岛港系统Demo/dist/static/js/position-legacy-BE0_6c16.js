/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
System.register(["./index-legacy-ZsikhjLK.js"],(function(t,e){"use strict";var n;return{setters:[t=>{n=t.aJ}],execute:function(){t("i",((t,e)=>{if(!n||!t||!e)return!1;const i=t.getBoundingClientRect();let o;return o=e instanceof Element?e.getBoundingClientRect():{top:0,right:window.innerWidth,bottom:window.innerHeight,left:0},i.top<o.bottom&&i.bottom>o.top&&i.right>o.left&&i.left<o.right}));const e=t=>{let e=0,n=t;for(;n;)e+=n.offsetTop,n=n.offsetParent;return e};t("a",((t,n)=>Math.abs(e(t)-e(n)))),t("g",(t=>{let e,n;return"touchend"===t.type?(n=t.changedTouches[0].clientY,e=t.changedTouches[0].clientX):t.type.startsWith("touch")?(n=t.touches[0].clientY,e=t.touches[0].clientX):(n=t.clientY,e=t.clientX),{clientX:e,clientY:n}}))}}}));
