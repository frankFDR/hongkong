/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
System.register(["./index-legacy-BevTx7Nc.js","./index-legacy-ZsikhjLK.js"],(function(e,n){"use strict";var t,a,r,o;return{setters:[e=>{t=e.E},e=>{a=e.d,r=e.g,o=e.f}],execute:function(){e("_",a({__name:"TreeSelectable",setup(e){let n=1;const a={label:"name",children:"zones"},l=(e,t)=>{if(0===e.level)return t([{name:"Root1"},{name:"Root2"}]);if(e.level>3)return t([]);let a=!1;a="region1"===e.data.name||"region2"!==e.data.name&&Math.random()>.5,setTimeout((()=>{let e;e=a?[{name:"zone"+n++},{name:"zone"+n++}]:[],t(e)}),500)};return(e,n)=>{const s=t;return o(),r(s,{lazy:"",load:l,props:a,"show-checkbox":""})}}}))}}}));
