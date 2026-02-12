import React, {useEffect, useState, useRef} from 'react'
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

  function validatePath(p){
    if(!p || p.trim().length<3){
      return 'Please enter a valid project path'
    }
    return ''
  }

  function startDetect(e){
    e && e.preventDefault()
    const v=validatePath(path)
    if(v){ setError(v); return }
    setError('')
    setScreen('detecting')
    setProgress(8)

    // simulate detection progress
    let p=8
    const iv=setInterval(()=>{
      p += Math.floor(Math.random()*12)+6
      if(p>=100){
        p=100
        setProgress(100)
        clearInterval(iv)
        // small timeout then show analysis
        setTimeout(()=>{
          setAnalysis({summary:'Analysis complete', conflicts:2, recommendations:3, report:'npm install\nmaparr analyze --fix'})
          setScreen('analysis')
          setToast('Analysis complete')
        },600)
      }else{
        setProgress(p)
      }
    },400)
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

  return (
    <div className="app-container" onKeyDown={(e)=>{/* reserved for future keyboard shortcuts */}}>
      {toast && <Toast message={toast} onClose={()=>setToast(null)} />}

      {screen==='landing' && (
        <section aria-labelledby="hero-title" className="hero card">
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
        <section className="card" aria-live="polite">
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
        <section className="card" aria-labelledby="analysis-heading">
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
        <section className="card" role="alert">
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
        <section className="card" role="alert">
          <div className="card-title">Analysis failed</div>
          <div className="card-sub">{error || 'Unexpected error'}</div>
          <div style={{marginTop:12}} className="row"><button className="btn btn-primary" onClick={retry}>Try again</button></div>
        </section>
      )}
    </div>
  )
}
