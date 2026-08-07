/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
import{V as t}from"./vue-json-viewer-91Zum-l8.js";import{_ as o,d as n,V as s,c as r,f as p,b as c}from"./index-BejtHq3K.js";import"./index-Dn3AG6ln.js";const d=n({components:{VabJsonViewer:t},props:{graphData:{type:Object,default:()=>{}}},data(){return{data:[]}},created(){this.data=JSON.parse(JSON.stringify([{edges:this.graphData.edges,nodes:this.graphData.nodes}]))}});function i(e,_,l,m,u,f){const a=s("vab-json-viewer");return p(),r("div",null,[c(a,{copyable:"","expand-depth":5,sort:"",value:e.data},null,8,["value"])])}const b=o(d,[["render",i]]);export{b as default};
