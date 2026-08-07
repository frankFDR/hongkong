/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
import{aJ as s}from"./index-BejtHq3K.js";const l=(t,i)=>{if(!s||!t||!i)return!1;const e=t.getBoundingClientRect();let n;return i instanceof Element?n=i.getBoundingClientRect():n={top:0,right:window.innerWidth,bottom:window.innerHeight,left:0},e.top<n.bottom&&e.bottom>n.top&&e.right>n.left&&e.left<n.right},o=t=>{let i=0,e=t;for(;e;)i+=e.offsetTop,e=e.offsetParent;return i},r=(t,i)=>Math.abs(o(t)-o(i)),f=t=>{let i,e;return t.type==="touchend"?(e=t.changedTouches[0].clientY,i=t.changedTouches[0].clientX):t.type.startsWith("touch")?(e=t.touches[0].clientY,i=t.touches[0].clientX):(e=t.clientY,i=t.clientX),{clientX:i,clientY:e}};export{r as a,f as g,l as i};
