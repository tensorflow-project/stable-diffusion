
#!/usr/bin/env python
# coding: utf-8

get_ipython().system('pip install -q git+https://github.com/keras-team/keras-cv.git')
get_ipython().system('pip install -q tensorflow==2.11.0')
get_ipython().system('pip install pyyaml h5py')

### clone our Github Repository
#get_ipython().system('git clone https://github.com/tensorflow-project/FineTuning')
#get_ipython().run_line_magic('cd', 'FineTuning/models')

import math
import random

import keras_cv
import numpy as np
import tensorflow as tf
from keras_cv import layers as cv_layers
from keras_cv.models.stable_diffusion import NoiseScheduler
from tensorflow import keras
import matplotlib.pyplot as plt
from numpy import dot
from numpy.linalg import norm
import os
import sys
from google.colab import drive
from PIL import Image
import shutil

###select path to find the models used here
py_file_location = "/content/FineTuning"
sys.path.append(os.path.abspath(py_file_location))
py_file_location = "/content/FineTuning/models"
sys.path.append(os.path.abspath(py_file_location))

### import the different models from our Github repository
#from text_encoder import TextEncoder
#from decoder import Decoder
#from diffusion_model import DiffusionModel
#from stable_diffusion import StableDiffusion
from models.text_encoder import TextEncoder
from models.decoder import Decoder
from models.diffusion_model import DiffusionModel
from models.stable_diffusion import StableDiffusion

### create an instance of the StableDiffusion() class
stable_diffusion = StableDiffusion()

def plot_images(images):
    """function to plot images in subplots
     Args: 
      - images (array): numpy arrays we want to visualize
    """
    plt.figure(figsize=(20, 20))
    for i in range(len(images)):
        ax = plt.subplot(1, len(images), i + 1)
        plt.imshow(images[i])
        plt.axis("off")
        

def assemble_image_dataset(urls):
    """Downloads a list of image URLs, resizes and normalizes the images, shuffles them, and adds random noise to create a 
    TensorFlow dataset object for them. 

    Args:
    - urls (list): A list of image URLs to download and use for the dataset

    Returns:
    - image_dataset (ds): A TensorFlow dataset object containing the preprocessed images

    Notes:
    - This function assumes that all images have the same dimensions and color channels
    """
  
    # Fetch all remote files
    files = [tf.keras.utils.get_file(origin=url) for url in urls]

    # Resize images
    resize = keras.layers.Resizing(height=512, width=512, crop_to_aspect_ratio=True)
    images = [keras.utils.load_img(img) for img in files]
    images = [keras.utils.img_to_array(img) for img in images]
    images = np.array([resize(img) for img in images])

    # The StableDiffusion image encoder requires images to be normalized to the
    # [-1, 1] pixel value range
    images = images / 127.5 - 1

    # Create the tf.data.Dataset
    image_dataset = tf.data.Dataset.from_tensor_slices(images)

    # Shuffle and introduce random noise
    image_dataset = image_dataset.shuffle(50, reshuffle_each_iteration=True)
    image_dataset = image_dataset.map(
        cv_layers.RandomCropAndResize(
            target_size=(512, 512),
            crop_area_factor=(0.8, 1.0),
            aspect_ratio_factor=(1.0, 1.0),
        ),
        num_parallel_calls=tf.data.AUTOTUNE,
    )
    image_dataset = image_dataset.map(
        cv_layers.RandomFlip(mode="horizontal"),
        num_parallel_calls=tf.data.AUTOTUNE,
    )
    return image_dataset

MAX_PROMPT_LENGTH = 77

### our new concept which is later inserted in the different prompts (for training and image generation)
placeholder_token_broccoli = "<my-broccoli-token>"
placeholder_token_emoji = "<my-emoji-token>"
placeholder_token_combined = "<my-broccoli-emoji-token>"


def pad_embedding(embedding):
    """Pads the input embedding with the end-of-text token to ensure that it has the same length as the maximum prompt length.

    Args:
    - embedding (list): A list of tokens representing the input embedding

    Returns:
    - padded_embedding (list): A list of tokens representing the padded input embedding
    """
    return embedding + (
        [stable_diffusion.tokenizer.end_of_text] * (MAX_PROMPT_LENGTH - len(embedding))
    )

### Add our placeholder_tokens to our stable_diffusion Model
stable_diffusion.tokenizer.add_tokens(placeholder_token_broccoli)
stable_diffusion.tokenizer.add_tokens(placeholder_token_emoji)
stable_diffusion.tokenizer.add_tokens(placeholder_token_combined)


def assemble_text_dataset(prompts, placeholder_token):
    """Creates a text dataset consisting of prompt embeddings. 
    
    Args:
    - prompts (str): A list of string prompts to be encoded and turned into embeddings
    - placeholder_token (str): our placeholder token
  
    Returns:
    - text_dataset: A text dataset containing the prompt embeddings
    """
    ### inserts our placeholder_token into the different prompts
    prompts = [prompt.format(placeholder_token) for prompt in prompts]
    
    ### prompts are tokenized and encoded and then added to the embedding
    embeddings = [stable_diffusion.tokenizer.encode(prompt) for prompt in prompts]
    embeddings = [np.array(pad_embedding(embedding)) for embedding in embeddings]
    
    ### creates a dataset consisting of the different prompt embeddings and shuffles it
    text_dataset = tf.data.Dataset.from_tensor_slices(embeddings)
    text_dataset = text_dataset.shuffle(100, reshuffle_each_iteration=True)
    return text_dataset
    
def assemble_dataset(urls, prompts, placeholder_token):
    """ Assembles a TensorFlow Dataset containing pairs of images and text prompts.

    Args:
    - urls: A list of URLs representing the image dataset
    - prompts: A list of text prompts corresponding to the images
    - placeholder_token: A string token representing the location where the prompt text will be inserted in the final text

    Returns:
    - A TensorFlow Dataset object containing pairs of images and their corresponding text prompts
    """
    ### creating the image and test dataset
    image_dataset = assemble_image_dataset(urls)
    text_dataset = assemble_text_dataset(prompts, placeholder_token)
    
    ### repeat both datasets to get several different combinations of images and text prompts
    # the image dataset is quite short, so we repeat it to match the length of the text prompt dataset
    image_dataset = image_dataset.repeat()

    # we use the text prompt dataset to determine the length of the dataset.  Due to
    # the fact that there are relatively few prompts we repeat the dataset 5 times.
    # we have found that this anecdotally improves results.
    text_dataset = text_dataset.repeat(5)
    return tf.data.Dataset.zip((image_dataset, text_dataset))    
    
def get_embedding(token):
    """Encodes a given token into a vector embedding using a pre-trained text encoder model.

    Args:
    - token (str): A single word or token to encode into a vector embedding

    Returns:
    - A tensor vector representing the embedding for the given token

    Raises:
    - ValueError: If the input token is empty or None
    """
    tokenized = stable_diffusion.tokenizer.encode(token)[1]
    embedding = stable_diffusion.text_encoder.layers[2].token_embedding(tf.constant(tokenized))

    return embedding

### create a dataset consisting of broccoli stickers prompts
broccoli_ds = assemble_dataset(
    urls = [
        "https://i.imgur.com/9zAwPyt.jpg",
        "https://i.imgur.com/qCNFRl4.jpg",
        "https://i.imgur.com/kPH9XIh.jpg",
        "https://i.imgur.com/qy1k0QK.jpg",
    ],
    prompts = [
        "a photo of a happy {}",
        "a photo of {}",
        "a photo of one {}",
        "a photo of a nice {}",
        "a good photo of a {}",
        "a photo of the nice {}",
        "a photo of a cool {}",
        "a rendition of the {}",
        "a nice sticker of a {}",
        "a sticker of a {}",
        "a sticker of a happy {}",
        "a sticker of a lucky {}",
        "a sticker of a lovely {}",
        "a sticker of a {} in a positive mood",
        "a pixar chracter of a satisfied {}",
        "a disney character of a positive {}",
        "a sticker of a delighted {}",
        "a sticker of a joyful {}",
        "a sticker of a cheerful {}",
        "a drawing of a glad {}",
        "a sticker of a merry {}",
        "a sticker of a pleased {}",
    ],
    placeholder_token = placeholder_token_broccoli
)  

### create a dataset consisting of happy emojis and happy prompts
emoji_ds = assemble_dataset(
    urls = [
        "https://i.imgur.com/BLLMggR.png",
        "https://i.imgur.com/PPQ2UtM.png",
        "https://i.imgur.com/6je73G3.png",
    ],
    prompts = [
        "a photo of a happy {}",
        "a photo of {}",
        "a photo of one {}",
        "a photo of a nice {}",
        "a good photo of a {}",
        "a photo of the nice {}",
        "a photo of a cool {}",
        "a rendition of the {}",
        "a nice emoji of a {}",
        "an emoji of a {}",
        "an emoji of a happy {}",
        "an emoji of a lucky {}",
        "an emoji of a lovely {}",
        "an emoji of a {} in a positive mood",
        "an emoji chracter of a satisfied {}",
        "an emoji character of a positive {}",
        "an emoji of a delighted {}",
        "an emoji of a joyful {}",
        "an emoji of a cheerful {}",
        "an emoji of a glad {}",
        "an emoji of a merry {}",
        "an emoji of a pleased {}",
    ],
    placeholder_token = placeholder_token_emoji
)

### concatenate the different datasets
train_ds = emoji_ds.concatenate(broccoli_ds)
train_ds = train_ds.batch(1).shuffle(
    train_ds.cardinality(), reshuffle_each_iteration=True)
    
### defining concept we want to build our new concept on
tokenized_initializer = stable_diffusion.tokenizer.encode("broccoli")[1]

### get the embedding of our basis concept to clone it to our new placeholder's embedding
new_weights_broccoli = stable_diffusion.text_encoder.layers[2].token_embedding(tf.constant(tokenized_initializer))

# Get len of .vocab instead of tokenizer
new_vocab_size = len(stable_diffusion.tokenizer.vocab)

# The embedding layer is the 2nd layer in the text encoder
### get the weights of the embedding layer
old_token_weights = stable_diffusion.text_encoder.layers[2].token_embedding.get_weights()
old_position_weights = stable_diffusion.text_encoder.layers[2].position_embedding.get_weights()

### unpack the old weights
old_token_weights = old_token_weights[0]

### old_token_weights has now the shape (vocab_size, embedding_dim)
### expand the dimension to be able to concatenate it with old_token_weights
new_weights_broccoli = np.expand_dims(new_weights_broccoli, axis=0)
new_weights_broccoli = np.concatenate([old_token_weights, new_weights_broccoli], axis=0)


### same for emoji token
### defining concept we want to build our new concept on 
tokenized_initializer_emoji = stable_diffusion.tokenizer.encode("emoji")[1]

new_weights_emoji = stable_diffusion.text_encoder.layers[2].token_embedding(tf.constant(tokenized_initializer_emoji))
new_weights_emoji = np.expand_dims(new_weights_emoji, axis=0)

### concatenate the weights for the new embedding at the end of our weights (~)
new_weights = np.concatenate([new_weights_broccoli, new_weights_emoji], axis=0)

tokenized_combined = stable_diffusion.tokenizer.encode("broccolis sticker")[1]

### combine new weights
new_weights_combined = stable_diffusion.text_encoder.layers[2].token_embedding(tf.constant(tokenized_combined))
new_weights_combined = np.expand_dims(new_weights_combined, axis=0)
new_weights = np.concatenate([new_weights, new_weights_combined], axis=0)

test_weights = stable_diffusion.text_encoder.layers[2].token_embedding.get_weights()
test_weights = test_weights[0]

# Have to set download_weights False so we can initialize the weights ourselves
### create a new text encoder 
new_encoder = TextEncoder(
    MAX_PROMPT_LENGTH,
    vocab_size = new_vocab_size,
    download_weights = False,
)

### we set the weights of the new_encoder to the same as in the old text_encoder except from the embedding layer
for index, layer in enumerate(stable_diffusion.text_encoder.layers):
    # Layer 2 is the embedding layer, so we omit it from our weight-copying
    if index == 2:
        continue
    new_encoder.layers[index].set_weights(layer.get_weights())

### set the weights of the embedding layer according to our new_weights
new_encoder.layers[2].token_embedding.set_weights([new_weights])

### set all weights of the other embeddings to the same values as in the initial text encoder
new_encoder.layers[2].position_embedding.set_weights(old_position_weights)

### set the stable_diffusion text encoder to our new_encoder and compile it
### thus the stable_diffusion.text_encoder has the adjusted weights
stable_diffusion._text_encoder = new_encoder
stable_diffusion._text_encoder.compile(jit_compile=True)


### we only train the encoder as we want to fine-tune the embeddings
stable_diffusion.diffusion_model.trainable = False
stable_diffusion.decoder.trainable = False
stable_diffusion.text_encoder.trainable = True

stable_diffusion.text_encoder.layers[2].trainable = True

def traverse_layers(layer):
    """ Traverses the layers and embedding attributes of a layer
    
    Args:
    - layer: A text encoder layer
    
    Yields:
    -  layers and their corresponding embedding attributes
    """
    if hasattr(layer, "layers"):
        for layer in layer.layers:
            yield layer
    if hasattr(layer, "token_embedding"):
        yield layer.token_embedding
    if hasattr(layer, "position_embedding"):
        yield layer.position_embedding

### iterates through the generator and adjusts the trainable attribute of the layers to trainable = True if it is part of the embedding
for layer in traverse_layers(stable_diffusion.text_encoder):
    if isinstance(layer, keras.layers.Embedding) or "clip_embedding" in layer.name:
        layer.trainable = True
    else:
        layer.trainable = False

### set the layer that only encodes the position of tokens in the prompts to trainable = False
new_encoder.layers[2].position_embedding.trainable = False

### put all the different components of stable diffusion model into a list
all_models = [
    stable_diffusion.text_encoder,
    stable_diffusion.diffusion_model,
    stable_diffusion.decoder,
]

# Remove the top layer from the encoder, which cuts off the variance and only returns the mean
### we make the encoder more efficient while still preserving the most important features
training_image_encoder = keras.Model(
    stable_diffusion.image_encoder.input,
    stable_diffusion.image_encoder.layers[-2].output,
)


def sample_from_encoder_outputs(outputs):
    """Returns a random sample from the embedding distribution given the mean and log variance tensors
    
    Args:
    - outputs (tensor): A tensor of shape (batch_size, embedding_dim*2), where the first embedding_dim values correspond to the mean of the distribution, 
               and the second embedding_dim values correspond to the log variance of the distribution
    
    Returns:
    - a tensor of shape (batch_size, embedding_dim), representing a random sample from the embedding distribution
    """
    mean, logvar = tf.split(outputs, 2, axis=-1)
    logvar = tf.clip_by_value(logvar, -30.0, 20.0)
    std = tf.exp(0.5 * logvar)
    sample = tf.random.normal(tf.shape(mean))
    return mean + std * sample


def get_timestep_embedding(timestep, dim=320, max_period=10000):
    """Returns the embedding of a specific timestep in the denoising process
    
    Args:
    - timestep (int): The timestep for which the embedding is requested
    - dim (int, optional): The dimensionality of the embedding, default is 320
    - max_period (int, optional): The maximum period, default is 10000
    
    Returns:
    - embedding (tf.Tensor): A tensor of shape (dim,) containing the embedding of the specified timestep
    """
    ### calculate half the dimensionality of the embedding
    half = dim // 2
    
    ### calculate frequencies using logarithmically decreasing values
    freqs = tf.math.exp(
        -math.log(max_period) * tf.range(0, half, dtype=tf.float32) / half
    )
    
    ### compute arguments for cosine and sine functions
    args = tf.convert_to_tensor([timestep], dtype=tf.float32) * freqs
    
    ### concatenate cosine and sine values to create embedding
    embedding = tf.concat([tf.math.cos(args), tf.math.sin(args)], 0)
    
    ### return the embedding tensor
    return embedding

#### used for hidden state (output of text encoder)
def get_position_ids():
    """returns position IDs for the transformer model,
        the IDs range from 0 to MAX_PROMPT_LENGTH-1
        
    Returns:
    - position_ids (tf.Tensor): A tensor of shape (1, MAX_PROMPT_LENGTH) containing the position IDs
    """
    
    ### create a list of integers from 0 to MAX_PROMPT_LENGTH-1
    positions = list(range(MAX_PROMPT_LENGTH))
    
    ### convert the list to a tensor with dtype int32
    position_ids = tf.convert_to_tensor([positions], dtype=tf.int32)
    
    return position_ids
    
    
class StableDiffusionFineTuner(keras.Model):
    """A Keras model for fine-tuning a Stable Diffusion model on a specific dataset.

    This model uses a Stable Diffusion model, which is a generative model that produces images by
    iteratively adding noise to an image. During training, the model predicts the amount of noise to add at each
    time step, based on the current image and a prompt (text input). This model fine-tunes the Stable Diffusion
    model on a specific dataset by training it on pairs of images and their corresponding prompts.

    Args:
    - stable_diffusion (StableDiffusion): The Stable Diffusion model to fine-tune
    - noise_scheduler (NoiseScheduler): A noise scheduler to determine the amount of noise to add at each time step
    """
    def __init__(self, stable_diffusion, noise_scheduler, **kwargs):
        super().__init__(**kwargs)
        self.stable_diffusion = stable_diffusion
        ### needed to calculate the amount of noise at a specific time step
        self.noise_scheduler = noise_scheduler

    def train_step(self, data):
        """Runs a single training step on the model.

        Args:
        - data (tuple): A tuple containing the training images and their corresponding
                text embeddings

        Returns:
        - A dictionary containing the current loss value.
        """
        images, embeddings = data

        with tf.GradientTape() as tape:
            # Sample from the predicted distribution for the training image
            latents = sample_from_encoder_outputs(training_image_encoder(images))
            # The latents must be downsampled to match the scale of the latents used
            # in the training of StableDiffusion.  This number is truly just a "magic"
            # constant that they chose when training the model.
            latents = latents * 0.18215

            # Produce random noise in the same shape as the latent sample
            noise = tf.random.normal(tf.shape(latents))
            ### get the batch dimension of our input data
            batch_dim = tf.shape(latents)[0]

            # Pick a random timestep for each sample in the batch
            ### for each sample in the batch we choose a different random timestep to later determine the specific timestep embedding
            timesteps = tf.random.uniform(
                (batch_dim,),
                minval=0,
                maxval=noise_scheduler.train_timesteps,
                dtype=tf.int64,
            )

            # Add noise to the latents based on the timestep for each sample
            ### using the scheduler to determine the amount of noise
            noisy_latents = self.noise_scheduler.add_noise(latents, noise, timesteps)

            # Encode the text in the training samples to use as hidden state in the diffusion model
            ### hidden state here means the output of the text encoder, thus the text embedding of our prompts
            encoder_hidden_state = self.stable_diffusion.text_encoder(
                [embeddings, get_position_ids()]
            )

            # Compute timestep embeddings for the randomly-selected timesteps for each sample in the batch
            timestep_embeddings = tf.map_fn(
                fn=get_timestep_embedding,
                elems=timesteps,
                fn_output_signature=tf.float32,
            )

            # Call the diffusion model
            ### calculate the noise predictions with help of the latents, the time step embeddings and the output of the encoder
            noise_pred = self.stable_diffusion.diffusion_model(
                [noisy_latents, timestep_embeddings, encoder_hidden_state]
            )

            # Compute the mean-squared error loss and reduce it
            ### by taking the mean
            loss = self.compiled_loss(noise_pred, noise)
            loss = tf.reduce_mean(loss, axis=2)
            loss = tf.reduce_mean(loss, axis=1)
            loss = tf.reduce_mean(loss)

        # Load the trainable weights and compute the gradients for them
        trainable_weights = self.stable_diffusion.text_encoder.trainable_weights
        grads = tape.gradient(loss, trainable_weights)

        # Gradients are stored in indexed slices, so we have to find the index
        # of the slice(s) which contain the placeholder token.
        index_of_placeholder_token = tf.reshape(tf.where(grads[0].indices == 49408), ())
        ### we only want to update the gradient of the placeholder token, therefore we create the tensor condition which has the value true for the index of the placeholder token (49408) and otherwise false
        condition = grads[0].indices == 49408
        ### add an extra dimension to later zero out the gradients for other tokens
        condition = tf.expand_dims(condition, axis=-1)

        # Override the gradients, zeroing out the gradients for all slices that
        # aren't for the placeholder token, effectively freezing the weights for
        # all other tokens.
        grads[0] = tf.IndexedSlices(
            values=tf.where(condition, grads[0].values, 0),
            indices=grads[0].indices,
            dense_shape=grads[0].dense_shape,
        )
        
        ### apply the gradients to the trainable weights of the encoder and thus only training the placeholder token's embedding
        self.optimizer.apply_gradients(zip(grads, trainable_weights))
        return {"loss": loss}
     

### beta is the diffusion rate
noise_scheduler = NoiseScheduler(
    ### beta_start determines the amount of noise added at the start of the denoising process
    beta_start=0.00085,
    ### beta_end at the end of the denoising process
    beta_end=0.012,
    ### the beta_schedule determines that the diffusion rate increases linearly
    beta_schedule="scaled_linear",
    train_timesteps=1000,
)

### Initialize the model we use to fine tune our concept
trainer = StableDiffusionFineTuner(stable_diffusion, noise_scheduler, name="trainer")
t = StableDiffusionFineTuner(stable_diffusion, noise_scheduler, name="t")


#EPOCHS = 50
### learning rate decays depending on the number of epochs to avoid convergence issues in few epochs 
### in the originial tutorial a scheduler is used but we experienced to have better results without a scheduler
"""learning_rate = keras.optimizers.schedules.CosineDecay(
    initial_learning_rate=1e-4, decay_steps=train_ds.cardinality() * EPOCHS
)"""
### inizialize the optimizer
optimizer = keras.optimizers.Adam(
    weight_decay=0.004, learning_rate=1e-4, epsilon=1e-8, global_clipnorm=10
)

trainer.compile(
    optimizer=optimizer,
    # We are performing reduction manually in our train step, so none is required here.
    loss=keras.losses.MeanSquaredError(reduction="none"),
)  


broccoli = []
emoji = []

placeholder_tokenized = stable_diffusion.tokenizer.encode(placeholder_token_combined)[1]
broccoli_tokenized = stable_diffusion.tokenizer.encode(placeholder_token_broccoli)[1]
emoji_tokenized = stable_diffusion.tokenizer.encode(placeholder_token_emoji)[1]


broccoli_embeddings = stable_diffusion.text_encoder.layers[2].token_embedding(tf.constant(broccoli_tokenized))
broccoli.append(broccoli_embeddings)
emoji_embeddings = stable_diffusion.text_encoder.layers[2].token_embedding(tf.constant(emoji_tokenized))
emoji.append(emoji_embeddings)

### define for later usage
old_weights = []
percent = 0.5

def percentage_emoji(percent):
    """Replaces a portion of the token embeddings in a StableDiffusion model's text encoder with emoji embeddings.

    The function takes a percentage value `percent` between 0 and 1 and computes a weighted sum of the original token
    embeddings and the emoji embeddings, where the weights are (1-percent) and percent, respectively. The resulting
    combined weights are then used to replace the last row of the token embedding matrix in the text encoder.

    Args:
    - percent (float): The percentage of emoji embeddings to use, as a float between 0 and 1

    Returns:
    - None
    """
    combined_weights = broccoli_embeddings + (percent*(emoji_embeddings - broccoli_embeddings))
    old_weights = stable_diffusion.text_encoder.layers[2].token_embedding.get_weights()
    old_weights = old_weights[0]
    old_weights[-1] = combined_weights

    stable_diffusion.text_encoder.layers[2].token_embedding.set_weights([old_weights])
    
def cosine_sim(e1,e2):
    """Calculate the cosine similarity between two vectors.

    Args:
    - e1 (array): First vector
    - e2 (array): Second vector

    Returns:
    - float: The cosine similarity between the two vectors
    """
    sim = dot(e1, e2)/(norm(e1)*norm(e2))
    return sim
 
### get embeddings
broccoli_embedding = get_embedding("broccoli")
placeholder_embedding = get_embedding(placeholder_token_broccoli)
emoji_embedding = get_embedding(placeholder_token_emoji)
combined_embedding = get_embedding(placeholder_token_combined)
### Compute the cosine similarity between the two embeddings
cosine_sim(broccoli_embedding, placeholder_embedding)
cosine_sim(placeholder_embedding, combined_embedding)
cosine_sim(emoji_embedding, combined_embedding)
                                                                     
old_broccoli = broccoli_embedding
old_combined = combined_embedding
old_placeholder = placeholder_embedding
old_emoji = emoji_embedding
                                                                

cosine_sim(old_broccoli, broccoli_embedding)
cosine_sim(old_combined, combined_embedding)
cosine_sim(old_placeholder, placeholder_embedding)
cosine_sim(old_emoji, emoji_embedding)
     
def image_generation(prompt, drive_folder, number):
    """Generates an image using stable diffusion model by passing a string with a placeholder token. 
    The generated image is saved as a JPG file and then copied to a Google Drive folder. A counter is used to ensure unique file names. 
    
    Args:
    - prompt (str): The prompt used for generating the image
    - drive_folder (str): The path to the Google Drive folder where the image will be saved
    - number (int): How many images are to be generated
    
    Returns:
    - None
    """
    ### get the number of the last image generated, to ensure each picture gets a different name
    i_file = os.path.join(drive_folder, 'i.txt')
    if os.path.isfile(i_file):
        with open(i_file, 'r') as f:
            i = int(f.read())
    else:
        i = 0
        
    for j in range(number):

        generated = stable_diffusion.text_to_image(
        prompt, batch_size=1,  num_steps=25 )
        broc = generated[0]

        ### convert the array generated from our stable diffusion model into a picture
        broc = Image.fromarray(broc, mode='RGB')

        broc.save(f'image_{i}.jpg')

        ### save the picture to Google Drive
        local_path = f'image_{i}.jpg'
        drive_path = os.path.join(drive_folder, f'image_{i}.jpg')  # Use f-string to include variable in file name
        shutil.copy(local_path, drive_path)

        ### store the value of i in the file, to ensure no picture will have the same name
        i += 1
        with open(i_file, 'w') as f:
            f.write(str(i))                                                                     
