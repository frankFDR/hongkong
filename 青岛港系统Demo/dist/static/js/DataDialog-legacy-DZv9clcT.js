/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
System.register(["./vue-json-viewer-legacy-C5vn8mrC.js","./index-legacy-ZsikhjLK.js","./index-legacy-B9r_RGDL.js"],(function(e,t){"use strict";var a,s,n,r,d,i,c;return{setters:[e=>{a=e.V},e=>{s=e._,n=e.d,r=e.V,d=e.c,i=e.f,c=e.b},null],execute:function(){const t=n({components:{VabJsonViewer:a},props:{graphData:{type:Object,default:()=>{}}},data:()=>({data:[]}),created(){this.data=JSON.parse(JSON.stringify([{edges:this.graphData.edges,nodes:this.graphData.nodes}]))}});e("default",s(t,[["render",function(e,t,a,s,n,o){const u=r("vab-json-viewer");return i(),d("div",null,[c(u,{copyable:"","expand-depth":5,sort:"",value:e.data},null,8,["value"])])}]]))}}}));
