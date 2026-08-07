/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
import{_ as c}from"./index-kQqDwBEu.js";import{_ as m,cC as _}from"./index-TT6O8i_P.js";import{d as i,U as u,bi as d,g as f,f as g,w as e,b as a,u as h,e as b,P as x}from"./index-BejtHq3K.js";import"./index-ngJ0ZP3x.js";const v=i({name:"VabChartWorldMap",__name:"VabChartWorldMap",props:{title:{type:String,default:""}},setup(n){const t=u({grid:{top:20,right:20,bottom:40,left:40}}),s=async()=>{const{data:o}=await x({url:"https://unpkg.com/echarts@4.9.0/map/json/world.json",method:"get"});setTimeout(()=>{_("world",o),t.geo={map:"world",type:"map",zoom:1.2,roam:!0}},500)};return d(()=>{s()}),(o,w)=>{const r=m,p=c,l=b;return g(),f(l,{lg:8,md:12,sm:24,xl:6,xs:24},{default:e(()=>[a(p,{"body-style":{height:"240px"},skeleton:"",title:n.title},{default:e(()=>[a(r,{option:h(t)},null,8,["option"])]),_:1},8,["title"])]),_:1})}}});export{v as default};
