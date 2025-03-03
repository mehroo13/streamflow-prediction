import streamlit as st
import pandas as pd
import numpy as np
import tensorflow as tf
import os
import tempfile
import matplotlib.pyplot as plt
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from io import BytesIO
from tensorflow.keras.utils import plot_model

# -------------------- Model Parameters --------------------
DEFAULT_GRU_UNITS = 64
DEFAULT_DENSE_UNITS = 32
DEFAULT_LEARNING_RATE = 0.001
DEFAULT_EPOCHS = 50
DEFAULT_BATCH_SIZE = 16
DEFAULT_TRAIN_SPLIT = 80
NUM_LAGGED_FEATURES = 3
MODEL_WEIGHTS_PATH = os.path.join(tempfile.gettempdir(), "gru_model_weights.weights.h5")
MODEL_PLOT_PATH = os.path.join(tempfile.gettempdir(), "gru_model_plot.png")

# -------------------- Metric Functions --------------------
def nse(actual, predicted):
    return 1 - (np.sum((actual - predicted) ** 2) / np.sum((actual - np.mean(actual)) ** 2))

def kge(actual, predicted):
    r = np.corrcoef(actual, predicted)[0, 1]
    alpha = np.std(predicted) / np.std(actual)
    beta = np.mean(predicted) / np.mean(actual)
    return 1 - np.sqrt((r - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2)

def pbias(actual, predicted):
    return 100 * (np.sum(predicted - actual) / np.sum(actual))

def peak_flow_error(actual, predicted):
    actual_peak = np.max(actual)
    predicted_peak = np.max(predicted)
    return (predicted_peak - actual_peak) / actual_peak * 100 if actual_peak != 0 else 0

def high_flow_bias(actual, predicted, percentile=90):
    threshold = np.percentile(actual, percentile)
    high_actual = actual[actual >= threshold]
    high_predicted = predicted[actual >= threshold]
    if len(high_actual) > 0:
        return 100 * (np.mean(high_predicted) - np.mean(high_actual)) / np.mean(high_actual)
    return 0

def low_flow_bias(actual, predicted, percentile=10):
    threshold = np.percentile(actual, percentile)
    low_actual = actual[actual <= threshold]
    low_predicted = predicted[actual <= threshold]
    if len(low_actual) > 0:
        return 100 * (np.mean(low_predicted) - np.mean(low_actual)) / np.mean(low_actual)
    return 0

def volume_error(actual, predicted):
    return 100 * (np.sum(predicted) - np.sum(actual)) / np.sum(actual)

# -------------------- Custom Callback for Epoch Tracking --------------------
class StreamlitProgressCallback(tf.keras.callbacks.Callback):
    def __init__(self, total_epochs, progress_placeholder):
        super().__init__()
        self.total_epochs = total_epochs
        self.progress_placeholder = progress_placeholder
        self.current_epoch = 0

    def on_epoch_end(self, epoch, logs=None):
        self.current_epoch = epoch + 1
        progress = self.current_epoch / self.total_epochs
        self.progress_placeholder.progress(min(progress, 1.0))
        self.progress_placeholder.text(f"Epoch {self.current_epoch}/{self.total_epochs} completed")

# -------------------- GRU Model Definition --------------------
def build_gru_model(input_shape, gru_layers, dense_layers, gru_units, dense_units, learning_rate):
    model = tf.keras.Sequential()
    for i in range(gru_layers):
        if i == 0:
            model.add(tf.keras.layers.GRU(gru_units[i], return_sequences=(i < gru_layers - 1), input_shape=input_shape))
        else:
            model.add(tf.keras.layers.GRU(gru_units[i], return_sequences=(i < gru_layers - 1)))
        model.add(tf.keras.layers.Dropout(0.2))
    for units in dense_units[:dense_layers]:
        model.add(tf.keras.layers.Dense(units, activation='relu'))
    model.add(tf.keras.layers.Dense(1))
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate), loss='mse')
    return model

# -------------------- Streamlit UI --------------------
st.set_page_config(page_title="Wateran", page_icon="🌊", layout="wide")
st.title("🌊 Wateran")
st.markdown("**General Time Series Prediction with GRU**")

# Initialize session state variables
if 'metrics' not in st.session_state:
    st.session_state.metrics = None
if 'train_results_df' not in st.session_state:
    st.session_state.train_results_df = None
if 'test_results_df' not in st.session_state:
    st.session_state.test_results_df = None
if 'fig' not in st.session_state:
    st.session_state.fig = None
if 'model_plot' not in st.session_state:
    st.session_state.model_plot = None
if 'scaler' not in st.session_state:
    st.session_state.scaler = None
if 'dynamic_inputs' not in st.session_state:
    st.session_state.dynamic_inputs = None
if 'static_inputs' not in st.session_state:
    st.session_state.static_inputs = None
if 'output_var' not in st.session_state:
    st.session_state.output_var = None
if 'new_predictions_df' not in st.session_state:
    st.session_state.new_predictions_df = None
if 'new_fig' not in st.session_state:
    st.session_state.new_fig = None
if 'gru_layers' not in st.session_state:
    st.session_state.gru_layers = None
if 'dense_layers' not in st.session_state:
    st.session_state.dense_layers = None
if 'gru_units' not in st.session_state:
    st.session_state.gru_units = None
if 'dense_units' not in st.session_state:
    st.session_state.dense_units = None
if 'learning_rate' not in st.session_state:
    st.session_state.learning_rate = None
if 'feature_cols' not in st.session_state:
    st.session_state.feature_cols = None
if 'new_data_file' not in st.session_state:
    st.session_state.new_data_file = None
if 'selected_inputs' not in st.session_state:
    st.session_state.selected_inputs = None
if 'new_date_col' not in st.session_state:
    st.session_state.new_date_col = None
if 'selected_metrics' not in st.session_state:
    st.session_state.selected_metrics = None

# Main layout with two columns
col1, col2 = st.columns([2, 1])

# Left Column: Data and Variable Selection
with col1:
    st.subheader("📥 Data Input")
    uploaded_file = st.file_uploader("Upload Training Data (Excel)", type=["xlsx"], key="train_data")
    
    if uploaded_file:
        df = pd.read_excel(uploaded_file)
        st.write("**Dataset Preview:**", df.head(5))
        
        # Date column selection
        datetime_cols = [col for col in df.columns if pd.api.types.is_datetime64_any_dtype(df[col]) or "date" in col.lower()]
        date_col = None
        if datetime_cols:
            date_col = st.selectbox("Select Date Column (optional)", ["None"] + datetime_cols, index=0, key="date_col_train")
            if date_col != "None":
                df[date_col] = pd.to_datetime(df[date_col])
                df = df.sort_values(date_col)
            else:
                st.info("Using index for ordering.")
        else:
            st.info("No datetime column detected. Using index.")
        
        # Numeric columns for variable selection
        numeric_cols = [col for col in df.columns if pd.api.types.is_numeric_dtype(df[col]) and (date_col is None or col != date_col)]
        if len(numeric_cols) < 2:
            st.error("Dataset requires at least two numeric columns.")
            st.stop()
        
        # Select output variable
        output_var = st.selectbox("🎯 Output Variable", numeric_cols, key="output_var_train")
        st.session_state.output_var = output_var
        available_input_cols = [col for col in numeric_cols if col != output_var]
        if not available_input_cols:
            st.error("No input variables available.")
            st.stop()
        
        st.markdown("#### Select Input Variables")
        # Use session state values if available; otherwise default to available_input_cols (or empty list)
        default_dynamic = st.session_state.dynamic_inputs if st.session_state.dynamic_inputs is not None else available_input_cols
        default_static = st.session_state.static_inputs if st.session_state.static_inputs is not None else []
        dynamic_inputs = st.multiselect("Select Dynamic Input Variables (lag features will be generated)",
                                        available_input_cols, default=default_dynamic, key="dynamic_inputs")
        static_inputs = st.multiselect("Select Static Input Variables (used without lag)",
                                       available_input_cols, default=default_static, key="static_inputs")
        
        # Check for overlap: if a variable is selected in both, treat it as dynamic.
        overlap = set(dynamic_inputs).intersection(set(static_inputs))
        if overlap:
            st.warning(f"Variables {', '.join(overlap)} selected in both dynamic and static. They will be treated as dynamic.")
            static_inputs = [var for var in static_inputs if var not in overlap]
        
        st.session_state.dynamic_inputs = dynamic_inputs
        st.session_state.static_inputs = static_inputs
        
        # Generate features: dynamic features get lagged and static remain unchanged.
        dynamic_feature_cols = []
        for var in dynamic_inputs + [output_var]:
            for lag in range(1, NUM_LAGGED_FEATURES + 1):
                df[f'{var}_Lag_{lag}'] = df[var].shift(lag)
                dynamic_feature_cols.append(f'{var}_Lag_{lag}')
        static_feature_cols = static_inputs
        full_feature_cols = static_feature_cols + dynamic_feature_cols
        st.session_state.feature_cols = full_feature_cols

        df.dropna(inplace=True)

# Right Column: Model Settings and Actions
with col2:
    st.subheader("⚙️ Model Configuration")
    
    # Training Parameters
    epochs = st.slider("Epochs", 1, 1500, DEFAULT_EPOCHS, step=10)
    batch_size = st.slider("Batch Size", 8, 128, DEFAULT_BATCH_SIZE, step=8)
    train_split = st.slider("Training Data %", 50, 90, DEFAULT_TRAIN_SPLIT) / 100
    
    # Model Architecture
    with st.expander("Model Architecture", expanded=False):
        gru_layers = st.number_input("GRU Layers", min_value=1, max_value=5, value=1, step=1)
        gru_units = [st.number_input(f"GRU Layer {i+1} Units", min_value=8, max_value=512, value=DEFAULT_GRU_UNITS, step=8, key=f"gru_{i}") for i in range(gru_layers)]
        dense_layers = st.number_input("Dense Layers", min_value=1, max_value=5, value=1, step=1)
        dense_units = [st.number_input(f"Dense Layer {i+1} Units", min_value=8, max_value=512, value=DEFAULT_DENSE_UNITS, step=8, key=f"dense_{i}") for i in range(dense_layers)]
        learning_rate = st.number_input("Learning Rate", min_value=0.00001, max_value=0.1, value=DEFAULT_LEARNING_RATE, format="%.5f")
    
    # Metrics Selection
    st.markdown("**Evaluation Metrics**")
    all_metrics = ["RMSE", "MAE", "R²", "NSE", "KGE", "PBIAS", "Peak Flow Error", "High Flow Bias", "Low Flow Bias", "Volume Error"]
    if st.session_state.selected_metrics is None:
        st.session_state.selected_metrics = all_metrics
    selected_metrics = st.multiselect("Select Metrics", all_metrics, default=st.session_state.selected_metrics, key="metrics_select")
    st.session_state.selected_metrics = selected_metrics
    if not selected_metrics:
        st.error("Please select at least one metric.")
        st.stop()

    # Store model settings in session state
    st.session_state.gru_layers = gru_layers
    st.session_state.dense_layers = dense_layers
    st.session_state.gru_units = gru_units
    st.session_state.dense_units = dense_units
    st.session_state.learning_rate = learning_rate

    # Training and Testing Buttons
    if uploaded_file:
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("🚀 Train Model"):
                train_size = int(len(df) * train_split)
                train_df, test_df = df[:train_size], df[train_size:]
                all_feature_cols = st.session_state.feature_cols
                scaler = MinMaxScaler()
                train_scaled = scaler.fit_transform(train_df[[st.session_state.output_var] + all_feature_cols])
                test_scaled = scaler.transform(test_df[[st.session_state.output_var] + all_feature_cols])
                st.session_state.scaler = scaler
                X_train, y_train = train_scaled[:, 1:], train_scaled[:, 0]
                X_test, y_test = test_scaled[:, 1:], test_scaled[:, 0]
                X_train = X_train.reshape((X_train.shape[0], 1, X_train.shape[1]))
                X_test = X_test.reshape((X_test.shape[0], 1, X_test.shape[1]))

                model = build_gru_model((X_train.shape[1], X_train.shape[2]), gru_layers, dense_layers, gru_units, dense_units, learning_rate)
                try:
                    with st.spinner("Training in progress..."):
                        progress_placeholder = st.empty()
                        callback = StreamlitProgressCallback(epochs, progress_placeholder)
                        model.fit(X_train, y_train, epochs=epochs, batch_size=batch_size, verbose=0, callbacks=[callback])
                        os.makedirs(os.path.dirname(MODEL_WEIGHTS_PATH), exist_ok=True)
                        model.save_weights(MODEL_WEIGHTS_PATH)
                    st.success("Model trained successfully!")
                except Exception as e:
                    st.error(f"Training failed: {str(e)}")

        with col_btn2:
            if st.button("🔍 Test Model"):
                if not os.path.exists(MODEL_WEIGHTS_PATH):
                    st.error("Train the model first!")
                    st.stop()
                train_size = int(len(df) * train_split)
                train_df, test_df = df[:train_size], df[train_size:]
                all_feature_cols = st.session_state.feature_cols
                scaler = st.session_state.scaler
                train_scaled = scaler.transform(train_df[[st.session_state.output_var] + all_feature_cols])
                test_scaled = scaler.transform(test_df[[st.session_state.output_var] + all_feature_cols])
                X_train, y_train = train_scaled[:, 1:], train_scaled[:, 0]
                X_test, y_test = test_scaled[:, 1:], test_scaled[:, 0]
                X_train = X_train.reshape((X_train.shape[0], 1, X_train.shape[1]))
                X_test = X_test.reshape((X_test.shape[0], 1, X_test.shape[1]))

                model = build_gru_model((X_train.shape[1], X_train.shape[2]), gru_layers, dense_layers, gru_units, dense_units, learning_rate)
                try:
                    model.load_weights(MODEL_WEIGHTS_PATH)
                    y_train_pred = model.predict(X_train)
                    y_test_pred = model.predict(X_test)
                    y_train_pred = scaler.inverse_transform(np.hstack([y_train_pred, X_train[:, 0, :]]))[:, 0]
                    y_test_pred = scaler.inverse_transform(np.hstack([y_test_pred, X_test[:, 0, :]]))[:, 0]
                    y_train_actual = scaler.inverse_transform(np.hstack([y_train.reshape(-1, 1), X_train[:, 0, :]]))[:, 0]
                    y_test_actual = scaler.inverse_transform(np.hstack([y_test.reshape(-1, 1), X_test[:, 0, :]]))[:, 0]
                    y_train_pred = np.clip(y_train_pred, 0, None)
                    y_test_pred = np.clip(y_test_pred, 0, None)

                    all_metrics_dict = {
                        "RMSE": lambda a, p: np.sqrt(mean_squared_error(a, p)),
                        "MAE": lambda a, p: mean_absolute_error(a, p),
                        "R²": lambda a, p: r2_score(a, p),
                        "NSE": nse,
                        "KGE": kge,
                        "PBIAS": pbias,
                        "Peak Flow Error": peak_flow_error,
                        "High Flow Bias": high_flow_bias,
                        "Low Flow Bias": low_flow_bias,
                        "Volume Error": volume_error
                    }

                    metrics = {metric: {
                        "Training": all_metrics_dict[metric](y_train_actual, y_train_pred),
                        "Testing": all_metrics_dict[metric](y_test_actual, y_test_pred)
                    } for metric in selected_metrics}
                    st.session_state.metrics = metrics

                    dates = df[date_col] if date_col != "None" else pd.RangeIndex(len(df))
                    train_dates, test_dates = dates[:train_size], dates[train_size:]
                    st.session_state.train_results_df = pd.DataFrame({
                        "Date": train_dates[:len(y_train_actual)],
                        f"Actual_{st.session_state.output_var}": y_train_actual,
                        f"Predicted_{st.session_state.output_var}": y_train_pred
                    })
                    st.session_state.test_results_df = pd.DataFrame({
                        "Date": test_dates[:len(y_test_actual)],
                        f"Actual_{st.session_state.output_var}": y_test_actual,
                        f"Predicted_{st.session_state.output_var}": y_test_pred
                    })
                    fig, ax = plt.subplots(2, 1, figsize=(12, 8))
                    ax[0].plot(train_dates[:len(y_train_actual)], y_train_actual, label="Actual", color="#1f77b4", linewidth=2)
                    ax[0].plot(train_dates[:len(y_train_pred)], y_train_pred, label="Predicted", color="#ff7f0e", linestyle="--", linewidth=2)
                    ax[0].set_title(f"Training: {st.session_state.output_var}", fontsize=14)
                    ax[0].legend()
                    ax[0].grid(True, linestyle='--', alpha=0.7)
                    if date_col != "None":
                        ax[0].set_xlabel("Date")
                        plt.setp(ax[0].xaxis.get_majorticklabels(), rotation=45)
                    ax[1].plot(test_dates[:len(y_test_actual)], y_test_actual, label="Actual", color="#1f77b4", linewidth=2)
                    ax[1].plot(test_dates[:len(y_test_pred)], y_test_pred, label="Predicted", color="#ff7f0e", linestyle="--", linewidth=2)
                    ax[1].set_title(f"Testing: {st.session_state.output_var}", fontsize=14)
                    ax[1].legend()
                    ax[1].grid(True, linestyle='--', alpha=0.7)
                    if date_col != "None":
                        ax[1].set_xlabel("Date")
                        plt.setp(ax[1].xaxis.get_majorticklabels(), rotation=45)
                    plt.tight_layout()
                    st.session_state.fig = fig
                    st.success("Model tested successfully!")
                except Exception as e:
                    st.error(f"Testing failed: {str(e)}")

# Results Section
if st.session_state.metrics or st.session_state.fig or st.session_state.train_results_df or st.session_state.test_results_df:
    with st.expander("📊 Results", expanded=True):
        if st.session_state.metrics is not None:
            st.subheader("📏 Performance Metrics")
            metrics_df = pd.DataFrame({
                "Metric": st.session_state.selected_metrics,
                "Training": [f"{st.session_state.metrics[m]['Training']:.4f}" for m in st.session_state.selected_metrics],
                "Testing": [f"{st.session_state.metrics[m]['Testing']:.4f}" for m in st.session_state.selected_metrics]
            })
            st.table(metrics_df.style.set_properties(**{'text-align': 'center'}).set_table_styles([
                {'selector': 'th', 'props': [('font-weight', 'bold'), ('text-align', 'center')]}
            ]))
        else:
            st.info("No results yet. Train and test the model to see metrics and plots.")

        col_plot, col_dl = st.columns([3, 1])
        with col_plot:
            if st.session_state.fig:
                st.subheader("📈 Prediction Plots")
                st.pyplot(st.session_state.fig)
        with col_dl:
            if st.session_state.fig:
                buf = BytesIO()
                st.session_state.fig.savefig(buf, format="png", dpi=300, bbox_inches='tight')
                st.download_button("⬇️ Download Plot", buf.getvalue(), "prediction_plot.png", "image/png", key="plot_dl")
            if st.session_state.train_results_df is not None:
                train_csv = st.session_state.train_results_df.to_csv(index=False)
                st.download_button("⬇️ Train Data CSV", train_csv, "train_predictions.csv", "text/csv", key="train_dl")
            if st.session_state.test_results_df is not None:
                test_csv = st.session_state.test_results_df.to_csv(index=False)
                st.download_button("⬇️ Test Data CSV", test_csv, "test_predictions.csv", "text/csv", key="test_dl")

# New Data Prediction Section
if os.path.exists(MODEL_WEIGHTS_PATH):
    with st.expander("🔮 New Predictions", expanded=False):
        st.subheader("Predict New Data")
        new_data_file = st.file_uploader("Upload New Data (Excel)", type=["xlsx"], key="new_data")
        
        if new_data_file and new_data_file != st.session_state.new_data_file:
            st.session_state.new_data_file = new_data_file
            st.session_state.new_predictions_df = None
            st.session_state.new_fig = None
            st.session_state.selected_inputs = None
            st.session_state.new_date_col = None

        if st.session_state.new_data_file:
            new_df = pd.read_excel(st.session_state.new_data_file)
            st.write("**New Data Preview:**", new_df.head())
            
            # Date column selection for new data
            datetime_cols = [col for col in new_df.columns if pd.api.types.is_datetime64_any_dtype(new_df[col]) or "date" in col.lower()]
            if datetime_cols:
                if st.session_state.new_date_col is None:
                    st.session_state.new_date_col = datetime_cols[0]
                date_col_new = st.selectbox("Select Date Column", datetime_cols, index=datetime_cols.index(st.session_state.new_date_col) if st.session_state.new_date_col in datetime_cols else 0, key="date_col_new")
                st.session_state.new_date_col = date_col_new
                new_df[date_col_new] = pd.to_datetime(new_df[date_col_new])
                new_df = new_df.sort_values(date_col_new)
            else:
                st.warning("No datetime column found. Predictions will use index.")
                date_col_new = None

            # Let the user select dynamic and static input variables for prediction
            new_numeric_cols = [col for col in new_df.columns if pd.api.types.is_numeric_dtype(new_df[col]) and (date_col_new is None or col != date_col_new)]
            default_new_dynamic = st.session_state.dynamic_inputs if st.session_state.dynamic_inputs is not None else new_numeric_cols
            default_new_static = st.session_state.static_inputs if st.session_state.static_inputs is not None else []
            new_dynamic_inputs = st.multiselect("Select Dynamic Input Variables for Prediction", 
                                                new_numeric_cols, default=default_new_dynamic, key="new_dynamic_inputs")
            new_static_inputs = st.multiselect("Select Static Input Variables for Prediction", 
                                               new_numeric_cols, default=default_new_static, key="new_static_inputs")
            overlap_new = set(new_dynamic_inputs).intersection(set(new_static_inputs))
            if overlap_new:
                st.warning(f"Variables {', '.join(overlap_new)} selected in both dynamic and static for new data. They will be treated as dynamic.")
                new_static_inputs = [var for var in new_static_inputs if var not in overlap_new]
            
            # Generate dynamic lag features for the selected dynamic inputs in new data
            dynamic_feature_cols_new = []
            for var in new_dynamic_inputs:
                for lag in range(1, NUM_LAGGED_FEATURES + 1):
                    new_df[f'{var}_Lag_{lag}'] = new_df[var].shift(lag)
                    dynamic_feature_cols_new.append(f'{var}_Lag_{lag}')
            # Static features remain unchanged
            static_feature_cols_new = new_static_inputs
            # Combine features (order matters for the scaler)
            new_full_feature_cols = static_feature_cols_new + dynamic_feature_cols_new
            
            # Create a dataframe with a dummy output column (needed for scaling)
            output_var = st.session_state.output_var
            full_new_df = pd.DataFrame(index=new_df.index, columns=[output_var] + new_full_feature_cols)
            full_new_df[output_var] = 0
            for col in new_full_feature_cols:
                if col in new_df.columns:
                    full_new_df[col] = new_df[col]
            full_new_df.fillna(0, inplace=True)
            
            # Use the scaler from training – note the training scaler was fitted on st.session_state.feature_cols.
            # For consistency, the new_full_feature_cols should match those used during training.
            scaler = st.session_state.scaler
            new_scaled = scaler.transform(full_new_df[[output_var] + st.session_state.feature_cols])
            X_new = new_scaled[:, 1:]
            X_new = X_new.reshape((X_new.shape[0], 1, X_new.shape[1]))
            
            model = build_gru_model((X_new.shape[1], X_new.shape[2]), st.session_state.gru_layers, st.session_state.dense_layers, st.session_state.gru_units, st.session_state.dense_units, st.session_state.learning_rate)
            model.load_weights(MODEL_WEIGHTS_PATH)
            y_new_pred = model.predict(X_new)
            y_new_pred = scaler.inverse_transform(np.hstack([y_new_pred, X_new[:, 0, :]]))[:, 0]
            y_new_pred = np.clip(y_new_pred, 0, None)
            
            dates_new = new_df[date_col_new] if date_col_new else pd.RangeIndex(len(new_df))
            st.session_state.new_predictions_df = pd.DataFrame({
                "Date": dates_new.values[-len(y_new_pred):],
                f"Predicted_{output_var}": y_new_pred
            })
            
            fig, ax = plt.subplots(figsize=(12, 4))
            if date_col_new:
                ax.plot(dates_new.values[-len(y_new_pred):], y_new_pred, label="Predicted", color="#ff7f0e", linewidth=2)
                ax.set_xlabel("Date")
                plt.xticks(rotation=45)
            else:
                ax.plot(y_new_pred, label="Predicted", color="#ff7f0e", linewidth=2)
                ax.set_xlabel("Index")
            ax.set_title(f"New Predictions: {output_var}", fontsize=14)
            ax.set_ylabel(output_var)
            ax.legend()
            ax.grid(True, linestyle='--', alpha=0.7)
            plt.tight_layout()
            st.session_state.new_fig = fig
                
            if st.session_state.new_predictions_df is not None:
                st.subheader("Prediction Results")
                st.write(st.session_state.new_predictions_df)
                col_new_plot, col_new_dl = st.columns([3, 1])
                with col_new_plot:
                    if st.session_state.new_fig:
                        st.pyplot(st.session_state.new_fig)
                with col_new_dl:
                    if st.session_state.new_fig:
                        buf = BytesIO()
                        st.session_state.new_fig.savefig(buf, format="png", dpi=300, bbox_inches='tight')
                        st.download_button("⬇️ Download Plot", buf.getvalue(), "new_prediction_plot.png", "image/png", key="new_plot_dl")
                    if st.session_state.new_predictions_df is not None:
                        new_csv = st.session_state.new_predictions_df.to_csv(index=False)
                        st.download_button("⬇️ Download CSV", new_csv, "new_predictions.csv", "text/csv", key="new_csv_dl")
                st.success("Predictions generated successfully!")

# Footer
st.markdown("---")
st.markdown("**Built with ❤️ by xAI | Powered by GRU and Streamlit**")
