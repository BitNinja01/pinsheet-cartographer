(function(){
var m=location.pathname.match(/^\/courses\/([^/]+)$/);
if(m&&document.querySelector(".round-actions")){
var enc=encodeURIComponent(decodeURIComponent(m[1]));
fetch("/plugins/cartographer/"+enc+"/actions-html").then(function(r){return r.text()}).then(function(h){
if(h){
var div=document.querySelector(".round-actions");
div.insertAdjacentHTML("beforeend",h);
}
});
}
})();
document.addEventListener("click",function(e){
var t=e.target.closest("[data-action=upload-osm]");
if(!t)return;
var inp=document.createElement("input");
inp.type="file";inp.accept=".osm";inp.style.display="none";
inp.addEventListener("change",function(){
var f=this.files[0];if(!f)return;
var fd=new FormData();fd.append("osm_file",f);
var btn=t;btn.textContent="Uploading...";
fetch("/plugins/cartographer/"+encodeURIComponent(t.getAttribute("data-course"))+"/upload-osm",{method:"POST",body:fd})
.then(function(r){if(r.ok){location.reload()}else{return r.json().then(function(d){btn.textContent=d.message||"Upload failed";setTimeout(function(){btn.textContent="Upload OSM"},3000)})}})
.catch(function(){btn.textContent="Network error";setTimeout(function(){btn.textContent="Upload OSM"},3000)});
});
document.body.appendChild(inp);inp.click();document.body.removeChild(inp);
});
document.addEventListener("click",function(e){
var t=e.target.closest("[data-action=delete-osm]");
if(!t)return;
e.preventDefault();
if(!confirm("Delete cached OSM and elevation data for this course?"))return;
var btn=t;var orig=btn.textContent;btn.textContent="Deleting...";
fetch("/plugins/cartographer/"+encodeURIComponent(t.getAttribute("data-course"))+"/osm",{method:"DELETE"})
.then(function(r){if(r.ok){location.reload()}else{return r.json().then(function(d){alert(d.message||"Delete failed");btn.textContent=orig})}})
.catch(function(){alert("Network error");btn.textContent=orig});
});
