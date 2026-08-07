/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
import{d as i,r as u,g as _,f as c,w as a,b as n,O as m,K as r,a as f}from"./index-BejtHq3K.js";import{E as x,g}from"./index-BpecoTZe.js";const y=i({__name:"UploadLimitCover",setup(v){const l=u(),d=t=>{var o,s;(o=l.value)==null||o.clearFiles();const e=t[0];e.uid=g(),(s=l.value)==null||s.handleStart(e)},p=()=>{var t;(t=l.value)==null||t.submit()};return(t,e)=>{const o=m,s=x;return c(),_(s,{ref_key:"upload",ref:l,action:"/uploadFile","auto-upload":!1,limit:1,"on-exceed":d},{trigger:a(()=>[n(o,{type:"primary"},{default:a(()=>e[0]||(e[0]=[r("选择文件")])),_:1,__:[0]})]),tip:a(()=>e[2]||(e[2]=[f("div",{class:"el-upload__tip"},"限制1个文件，新文件将覆盖旧文件",-1)])),default:a(()=>[n(o,{type:"success",onClick:p},{default:a(()=>e[1]||(e[1]=[r("上传到服务器")])),_:1,__:[1]})]),_:1},512)}}});export{y as _};
