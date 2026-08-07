/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
System.register(["./index-legacy-ZsikhjLK.js","./index-legacy-6wZvTweL.js"],(function(e,t){"use strict";var l,n,i,a,r,s,u,c,g,o,d,p,f,_;return{setters:[e=>{l=e.d,n=e.r,i=e.g,a=e.f,r=e.w,s=e.b,u=e.O,c=e.K,g=e.a,o=e.H,d=e.u,p=e.gP,f=e.fb},e=>{_=e.E}],execute:function(){e("_",l({__name:"UploadBasic",setup(e){const t=n([]),l=(e,t)=>{p.warning(`限制为3个文件，您选择了${e.length}个文件，加起来总共$${e.length+t.length}个文件`)},m=e=>f.confirm(`是否取消上传 ${e.name} ？`,{draggable:!0}).then((()=>!0),(()=>!1));return(e,n)=>{const p=u,f=_;return a(),i(f,{"file-list":d(t),"onUpdate:fileList":n[0]||(n[0]=e=>o(t)?t.value=e:null),action:"/uploadFile","before-remove":m,limit:3,multiple:"","on-exceed":l},{tip:r((()=>n[2]||(n[2]=[g("div",{class:"el-upload__tip"},"jpg/png 文件需小于500kb",-1)]))),default:r((()=>[s(p,{type:"primary"},{default:r((()=>n[1]||(n[1]=[c("点击上传")]))),_:1,__:[1]})])),_:1},8,["file-list"])}}}))}}}));
