/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
import{_ as r}from"./index-kQqDwBEu.js";import{_ as m,cC as _}from"./index-TT6O8i_P.js";import{d as l,U as u,bi as h,g as d,f,w as a,b as e,u as g,e as b,P as C}from"./index-BejtHq3K.js";import"./index-ngJ0ZP3x.js";const w=l({name:"VabChartChinaMap",__name:"VabChartChinaMap",props:{title:{type:String,default:""}},setup(n){const t=u({grid:{top:20,right:20,bottom:40,left:40}}),s=async()=>{const{data:o}=await C({url:"https://unpkg.com/echarts@4.9.0/map/json/china.json",method:"get"});setTimeout(()=>{_("china",o),t.geo={map:"china",type:"map",zoom:1.2,roam:!0}},500)};return h(()=>{s()}),(o,x)=>{const p=m,c=r,i=b;return f(),d(i,{lg:8,md:12,sm:24,xl:6,xs:24},{default:a(()=>[e(c,{"body-style":{height:"240px"},skeleton:"",title:n.title},{default:a(()=>[e(p,{option:g(t)},null,8,["option"])]),_:1},8,["title"])]),_:1})}}});export{w as default};
