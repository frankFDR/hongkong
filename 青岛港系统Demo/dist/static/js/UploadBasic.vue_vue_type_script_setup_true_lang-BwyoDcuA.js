/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
import{d as p,r as d,g as f,f as u,w as a,b as m,O as _,K as c,a as g,H as b,u as x,gP as B,fb as E}from"./index-BejtHq3K.js";import{E as v}from"./index-BpecoTZe.js";const N=p({__name:"UploadBasic",setup($){const o=d([]),s=(t,e)=>{B.warning("限制为3个文件，您选择了".concat(t.length,"个文件，加起来总共$").concat(t.length+e.length,"个文件"))},n=t=>E.confirm("是否取消上传 ".concat(t.name," ？"),{draggable:!0}).then(()=>!0,()=>!1);return(t,e)=>{const l=_,r=v;return u(),f(r,{"file-list":x(o),"onUpdate:fileList":e[0]||(e[0]=i=>b(o)?o.value=i:null),action:"/uploadFile","before-remove":n,limit:3,multiple:"","on-exceed":s},{tip:a(()=>e[2]||(e[2]=[g("div",{class:"el-upload__tip"},"jpg/png 文件需小于500kb",-1)])),default:a(()=>[m(l,{type:"primary"},{default:a(()=>e[1]||(e[1]=[c("点击上传")])),_:1,__:[1]})]),_:1},8,["file-list"])}}});export{N as _};
