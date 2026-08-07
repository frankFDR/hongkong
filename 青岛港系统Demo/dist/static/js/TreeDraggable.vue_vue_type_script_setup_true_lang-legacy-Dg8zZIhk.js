/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
System.register(["./index-legacy-BevTx7Nc.js","./index-legacy-ZsikhjLK.js"],(function(e,l){"use strict";var a,n,r,d;return{setters:[e=>{a=e.E},e=>{n=e.d,r=e.g,d=e.f}],execute:function(){e("_",n({__name:"TreeDraggable",setup(e){const l=(e,l,a)=>"二级 3-1"!==l.data.label||"inner"!==a,n=e=>!e.data.label.includes("三级 3-1-1"),t=[{label:"一级 1",children:[{label:"二级 1-1",children:[{label:"三级 1-1-1"}]}]},{label:"一级 2",children:[{label:"二级 2-1",children:[{label:"三级 2-1-1"}]},{label:"二级 2-2",children:[{label:"三级 2-2-1"}]}]},{label:"一级 3",children:[{label:"二级 3-1",children:[{label:"三级 3-1-1"}]},{label:"二级 3-2",children:[{label:"三级 3-2-1"}]}]}];return(e,b)=>{const c=a;return d(),r(c,{"allow-drag":n,"allow-drop":l,data:t,"default-expand-all":"",draggable:"","node-key":"id"})}}}))}}}));
