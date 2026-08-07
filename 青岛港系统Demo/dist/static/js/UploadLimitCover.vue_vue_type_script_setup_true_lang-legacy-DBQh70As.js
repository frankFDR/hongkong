/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
System.register(["./index-legacy-ZsikhjLK.js","./index-legacy-6wZvTweL.js"],(function(e,t){"use strict";var a,u,i,l,r,s,n,c,d,o,_;return{setters:[e=>{a=e.d,u=e.r,i=e.g,l=e.f,r=e.w,s=e.b,n=e.O,c=e.K,d=e.a},e=>{o=e.E,_=e.g}],execute:function(){e("_",a({__name:"UploadLimitCover",setup(e){const t=u(),a=e=>{t.value?.clearFiles();const a=e[0];a.uid=_(),t.value?.handleStart(a)},p=()=>{t.value?.submit()};return(e,u)=>{const _=n,f=o;return l(),i(f,{ref_key:"upload",ref:t,action:"/uploadFile","auto-upload":!1,limit:1,"on-exceed":a},{trigger:r((()=>[s(_,{type:"primary"},{default:r((()=>u[0]||(u[0]=[c("选择文件")]))),_:1,__:[0]})])),tip:r((()=>u[2]||(u[2]=[d("div",{class:"el-upload__tip"},"限制1个文件，新文件将覆盖旧文件",-1)]))),default:r((()=>[s(_,{type:"success",onClick:p},{default:r((()=>u[1]||(u[1]=[c("上传到服务器")]))),_:1,__:[1]})])),_:1},512)}}}))}}}));
