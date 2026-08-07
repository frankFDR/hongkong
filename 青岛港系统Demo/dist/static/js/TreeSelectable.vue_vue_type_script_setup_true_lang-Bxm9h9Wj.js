/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
import{E as s}from"./index-Cli3oeGQ.js";import{d as c,g as m,f as i}from"./index-BejtHq3K.js";const d=c({__name:"TreeSelectable",setup(f){let n=1;const l={label:"name",children:"zones"},r=(a,t)=>{if(a.level===0)return t([{name:"Root1"},{name:"Root2"}]);if(a.level>3)return t([]);let e=!1;a.data.name==="region1"?e=!0:a.data.name==="region2"?e=!1:e=Math.random()>.5,setTimeout(()=>{let o;e?o=[{name:"zone".concat(n++)},{name:"zone".concat(n++)}]:o=[],t(o)},500)};return(a,t)=>{const e=s;return i(),m(e,{lazy:"",load:r,props:l,"show-checkbox":""})}}});export{d as _};
