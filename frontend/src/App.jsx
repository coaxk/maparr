import React, {useEffect, useState, useRef} from 'react'
import axios from 'axios'
import './App.css'
import './index.css'

function Spinner(){
  return <div className="spinner" role="status" aria-label="Loading"></div>
}

function ProgressBar({value=0}){
  return (
    <div className="progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} role="progressbar">
      <div className="bar" style={{width:`${value}%`}} />
    </div>
  )
}

function Tooltip({children, label}){
  return (
    <span className="tooltip" tabIndex={0} aria-label={label} title={label}>
      {children}
    </span>
  )
}

function Toast({message, onClose}){
  useEffect(()=>{
    const t=setTimeout(()=>onClose && onClose(), 3000)
    return ()=>clearTimeout(t)
  },[onClose])
  return (
    <div className="toast" role="status" aria-live="polite">{message}</div>
  )
}

function CodeBlock({code}){
  const [copied,setCopied]=useState(false)
  async function copy(){
    try{
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(()=>setCopied(false),1500)
    }catch(e){
      setCopied(false)
    }
  }
  return (
    <div className="code-block" aria-label="Code sample">
      <button className="btn btn-outline code-copy" onClick={copy} aria-label="Copy code">{copied? 'Copied' : 'Copy'}</button>
      <pre className="mono small" style={{margin:0,whiteSpace:'pre-wrap'}}>{code}</pre>
    </div>
  )
}

export default function App(){
  const [screen,setScreen]=useState('landing')
  const [path,setPath]=useState('')
  const [error,setError]=useState('')
  const [progress,setProgress]=useState(0)
  const [toast,setToast]=useState(null)
  const [analysis,setAnalysis]=useState(null)
  const headingRef = useRef(null)

  useEffect(()=>{ if(screen==='analysis' && headingRef.current) headingRef.current.focus() },[screen])

  // Check docker availability on mount
  useEffect(()=>{
    let mounted=true
    async function checkDocker(){
      try{
        const res = await axios.get('/api/docker/status')
        if(!mounted) return
        if(res?.data?.ok!==true){ setScreen('dockerError'); setToast('Docker not available') }
      }catch(err){
        if(!mounted) return
        setScreen('dockerError')
      }
    }
    checkDocker()
    return ()=>{ mounted=false }
  },[])

  function validatePath(p){
    if(!p || p.trim().length<3){
      return 'Please enter a valid project path'
    }
    return ''
  }

  async function startDetect(e){
    e && e.preventDefault()
    const v=validatePath(path)
    if(v){ setError(v); return }
    setError('')
    setScreen('detecting')
    setProgress(8)

    try{
      // call backend analyze endpoint
      const res = await axios.post('/api/analyze', {path})
      // optimistic progress
      setProgress(60)

      if(res?.data?.analysis){
        setAnalysis(res.data.analysis)
        setProgress(100)
        setTimeout(()=>{ setScreen('analysis'); setToast('Analysis complete') }, 300)
      }else if(res?.data?.error){
        setError(res.data.error)
        setScreen('error')
      }else if(res?.data?.jobId){
        // poll for job result (best-effort, backend may provide this)
        const jobId = res.data.jobId
        let done=false
        for(let i=0;i<20 && !done;i++){
          await new Promise(r=>setTimeout(r,800))
          try{
            const status = await axios.get(`/api/analyze/${jobId}/status`)
            if(status?.data?.ready){
              done=true
              setAnalysis(status.data.analysis)
              setProgress(100)
              setScreen('analysis')
              setToast('Analysis complete')
              break
            }else{
              setProgress(Math.min(95, 60 + i*2))
            }
          }catch(e){/* ignore transient errors */}
        }
        if(!done){ setError('Analysis timed out'); setScreen('error') }
      }else{
        // Fallback: if no useful payload, request recommendations endpoint
        const rec = await axios.get('/api/recommendations',{params:{path}})
        setAnalysis({summary:'Analysis (partial)', conflicts: rec?.data?.conflicts ?? 0, recommendations: rec?.data?.recommendations ?? 0, report: rec?.data?.report ?? ''})
        setProgress(100)
        setScreen('analysis')
        setToast('Partial analysis complete')
      }
    }catch(err){
      console.error(err)
      setError(err?.response?.data?.error || err.message || 'Analysis failed')
      setScreen('error')
    }
  }

  function useDefaults(){
    setPath('/workspace/project')
    setToast('Default path set')
  }

  function simulateDockerError(){
    setScreen('dockerError')
  }

  function retry(){
    setScreen('landing')
    setProgress(0)
    setAnalysis(null)
  }

  async function fetchRecommendations(){
    try{
      const res = await axios.get('/api/recommendations',{params:{path}})
      if(res?.data){
        setAnalysis(prev=>({...(prev||{}), recommendations: res.data.recommendations ?? prev?.recommendations, report: res.data.report ?? prev?.report}))
        setToast('Recommendations loaded')
      }
    }catch(e){
      // non-fatal
    }
  }

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <div id="main-content" className="app-container" role="main" onKeyDown={(e)=>{/* reserved for future keyboard shortcuts */}}>
      {toast && <Toast message={toast} onClose={()=>setToast(null)} />}

      {screen==='landing' && (
        <section aria-labelledby="hero-title" className="hero card" role="region" aria-label="Landing">
          <div className="card-header">
            <div>
              <h1 id="hero-title">Welcome to MapArr</h1>
              <div className="card-sub">Analyze and reconcile mapping conflicts quickly.</div>
            </div>
            <div className="row">
              <button className="btn btn-ghost" onClick={simulateDockerError} aria-label="Simulate docker error">Simulate Error</button>
              <button className="btn btn-primary" onClick={()=>{setToast('Starting quick detect'); useDefaults(); startDetect()}}>Quick Detect</button>
            </div>
          </div>

          <form onSubmit={startDetect} className="col" aria-label="Detection form">
            <label className="label" htmlFor="pathInput">Project path (manual)</label>
            <input id="pathInput" className="input" value={path} onChange={(e)=>setPath(e.target.value)} placeholder="/path/to/project or C:\\project" aria-required="true" aria-invalid={!!error} />
            {error && <div role="alert" style={{color:'var(--danger)'}}>{error}</div>}

            <div className="row" style={{marginTop:12}}>
              <button type="submit" className="btn btn-primary">Detect</button>
              <button type="button" className="btn btn-outline" onClick={useDefaults}>Use defaults</button>
            </div>
          </form>

          <div style={{marginTop:16}} className="muted small">Tips: Use the manual path for custom projects. Hover options for details.</div>
        </section>
      )}

      {screen==='detecting' && (
        <section className="card" aria-live="polite" role="region" aria-label="Detecting"> 
          <div className="card-header">
            <div>
              <div className="card-title">Detecting environment…</div>
              <div className="card-sub">Probing project files and containers</div>
            </div>
            <div className="row"><Spinner/></div>
          </div>

          <div style={{marginTop:8}}><ProgressBar value={progress} /></div>
          <div className="small muted" style={{marginTop:8}}>Progress: {progress}%</div>
        </section>
      )}

      {screen==='analysis' && analysis && (
        <section className="card" aria-labelledby="analysis-heading" role="region" aria-label="Analysis Summary">
          <div className="card-header">
            <div>
              <h2 id="analysis-heading" tabIndex={-1} ref={headingRef}>Analysis Summary</h2>
              <div className="card-sub">High level findings and recommended fixes</div>
            </div>
            <div className="row">
              <Tooltip label="Export report"><button className="btn btn-outline" onClick={()=>{setToast('Report exported')}}>Export</button></Tooltip>
              <button className="btn btn-ghost" onClick={retry}>Done</button>
            </div>
          </div>

          <div className="list">
            <div className="card">
              <div className="card-title">Summary</div>
              <div className="card-sub">{analysis.summary}</div>
              <div style={{marginTop:8}}>
                <span className="badge good">OK</span>
              </div>
            </div>

            <div className="card">
              <div className="card-title">Conflicts</div>
              <div className="card-sub">{analysis.conflicts} potential conflicts detected</div>
              <div style={{marginTop:8}}>
                <button className="btn btn-danger" onClick={()=>setToast('Marked for manual review')}>Mark Review</button>
              </div>
            </div>

            <div className="card">
              <div className="card-title">Recommendations</div>
              <div className="card-sub">{analysis.recommendations} recommended fixes</div>
              <div style={{marginTop:8}}>
                <CodeBlock code={analysis.report} />
              </div>
            </div>

            <div className="card">
              <div className="card-title">Actions</div>
              <div style={{marginTop:8}} className="row">
                <button className="btn btn-primary" onClick={()=>setToast('Apply fixes not implemented in demo')}>Apply Fixes</button>
                <button className="btn btn-outline" onClick={()=>setToast('Download report')}>Download</button>
              </div>
            </div>
          </div>
        </section>
      )}

      {screen==='dockerError' && (
        <section className="card" role="alert" aria-label="Docker error"> 
          <div className="card-header">
            <div>
              <div className="card-title">Docker not available</div>
              <div className="card-sub">MapArr requires Docker to run certain checks</div>
            </div>
            <div className="row">
              <button className="btn btn-primary" onClick={retry}>Retry</button>
              <button className="btn btn-outline" onClick={()=>setScreen('analysis')}>Skip checks</button>
            </div>
          </div>
          <div className="small muted" style={{marginTop:8}}>Troubleshooting: Ensure Docker Desktop is running and accessible to your user.</div>
        </section>
      )}

      {screen==='error' && (
        <section className="card" role="alert" aria-label="Analysis failed"> 
          <div className="card-title">Analysis failed</div>
          <div className="card-sub">{error || 'Unexpected error'}</div>
          <div style={{marginTop:12}} className="row"><button className="btn btn-primary" onClick={retry}>Try again</button></div>
        </section>
      )}
      </div>
    </>
  )
}
