use axum::{extract::State, response::Json, routing::{get, post}, Router};
use serde::{Serialize, Deserialize};
use std::{sync::{Arc, Mutex}};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::TcpListener,
    sync::Mutex as AsyncMutex,
    task,
};
use tokio_serial::{SerialPortBuilderExt, SerialStream};

#[derive(Clone, Serialize)]
struct SensorData {
    values: Vec<f64>,
}

#[derive(Clone, Serialize)]
struct HistoryData {
    data: Vec<Vec<f64>>,
}

#[derive(Clone)]
struct AppState {
    latest_data: Arc<Mutex<Vec<f64>>>,
    history: Arc<Mutex<Vec<Vec<f64>>>>,
    serial_tx: Arc<AsyncMutex<Option<SerialStream>>>,
}

#[derive(Deserialize)]
struct PortRequest {
    port: String,
}

#[tokio::main]
async fn main() {
    let state = AppState {
        latest_data: Arc::new(Mutex::new(vec![])),
        history: Arc::new(Mutex::new(vec![])),
        serial_tx: Arc::new(AsyncMutex::new(None)),
    };

    // TCP Server Spawn (Port 8081) for Arduino Data
    let state_clone_for_tcp = state.clone();
    task::spawn(async move {
        start_data_server(state_clone_for_tcp).await;
    });

    // HTTP Server (Port 8000) for GUI Python
    let app = Router::new()
        .route("/connect", post(connect_serial))
        .route("/start_sampling", post(send_start_command))
        .route("/stop_sampling", post(send_stop_command))
        .route("/disconnect", post(disconnect_serial))
        .route("/data", get(get_data))
        .route("/history", get(get_history))
        .route("/reset", post(reset_history))
        .with_state(state);

    println!("BACKEND RUNNING:");
    println!(" - Command via Serial USB");
    println!(" - Data via WiFi (Port 8081)");
    println!(" - GUI API: http://127.0.0.1:8000");

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8000").await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

// Serial Logic
async fn connect_serial(State(state): State<AppState>, Json(payload): Json<PortRequest>) -> Json<&'static str> {
    let port_name = payload.port;
    println!("Connecting Serial: {}", port_name);

    match tokio_serial::new(&port_name, 9600).open_native_async() {
        Ok(port) => {
            *state.serial_tx.lock().await = Some(port);
            Json("Connected")
        }
        Err(e) => {
            println!("Serial Error: {}", e);
            Json("Failed")
        }
    }
}

async fn send_start_command(State(state): State<AppState>) -> Json<&'static str> {
    let mut guard = state.serial_tx.lock().await;
    if let Some(port) = guard.as_mut() {
        let _ = port.write_all(b"START_SAMPLING\n").await;
        println!("Sent: START_SAMPLING");
        Json("Sent")
    } else {
        Json("No Serial")
    }
}

async fn send_stop_command(State(state): State<AppState>) -> Json<&'static str> {
    let mut guard = state.serial_tx.lock().await;
    if let Some(port) = guard.as_mut() {
        let _ = port.write_all(b"STOP_SAMPLING\n").await;
        println!("Sent: STOP_SAMPLING");
        Json("Sent")
    } else {
        Json("No Serial")
    }
}

async fn disconnect_serial(State(state): State<AppState>) -> Json<&'static str> {
    *state.serial_tx.lock().await = None;
    Json("Disconnected")
}

// Data Handlers
async fn start_data_server(state: AppState) {
    let listener = TcpListener::bind("0.0.0.0:8081").await.unwrap();
    
    loop {
        if let Ok((socket, addr)) = listener.accept().await {
            println!("[WiFi] Connected: {}", addr);
            let latest_data = state.latest_data.clone();
            let history = state.history.clone();
            
            task::spawn(async move {
                let mut reader = BufReader::new(socket);
                let mut line = String::new();
                loop {
                    line.clear();
                    match reader.read_line(&mut line).await {
                        Ok(0) => break,
                        Ok(_) => {
                            // Arduino send: val1,val2,...,state,level
                            let values: Vec<f64> = line.trim().split(',')
                                .filter_map(|v| v.parse().ok())
                                .collect();

                            // Take 7 sensor data
                            if values.len() >= 7 {
                                let sensor_vals: Vec<f64> = values.into_iter().take(7).collect();
                                *latest_data.lock().unwrap() = sensor_vals.clone(); // Update new data for live graph frontend
                                history.lock().unwrap().push(sensor_vals); // Save to history
                            }
                        }
                        Err(_) => break,
                    }
                }
                println!("[WiFi] Disconnected");
            });
        }
    }
}

// Endpoint for live graph
async fn get_data(State(state): State<AppState>) -> Json<SensorData> {
    let data = state.latest_data.lock().unwrap().clone();
    Json(SensorData { values: data })
}

// Endpoint for export
async fn get_history(State(state): State<AppState>) -> Json<HistoryData> {
    let data = state.history.lock().unwrap().clone();
    Json(HistoryData { data })
}

// Endpoint for reset
async fn reset_history(State(state): State<AppState>) -> Json<&'static str> {
    state.history.lock().unwrap().clear();
    *state.latest_data.lock().unwrap() = vec![];
    println!("Data Cleared from Backend Memory");
    Json("Cleared")
}