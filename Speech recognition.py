# -*- coding: utf-8 -*-
"""proj.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1h9zxuLg6r3oaCAUNg3b_FerTOzIg_NS0
"""

from google.colab import drive
drive.mount('/content/drive')

import os
import torch
import torch.nn as nn
import torch.utils.data as data
import torch.optim as optim
import torch.nn.functional as F
import torchaudio
import numpy as np

"""**Text Transformation and Error Rate Computation for Speech Recognition**"""

def avg_wer(wer_scores, combined_ref_len):
    return float(sum(wer_scores)) / float(combined_ref_len)


def _levenshtein_distance(ref, hyp):

    m = len(ref)
    n = len(hyp)

    # special case
    if ref == hyp:
        return 0
    if m == 0:
        return n
    if n == 0:
        return m

    if m < n:
        ref, hyp = hyp, ref
        m, n = n, m

    # use O(min(m, n)) space
    distance = np.zeros((2, n + 1), dtype=np.int32)

    # initialize distance matrix
    for j in range(0,n + 1):
        distance[0][j] = j

    # calculate levenshtein distance
    for i in range(1, m + 1):
        prev_row_idx = (i - 1) % 2
        cur_row_idx = i % 2
        distance[cur_row_idx][0] = i
        for j in range(1, n + 1):
            if ref[i - 1] == hyp[j - 1]:
                distance[cur_row_idx][j] = distance[prev_row_idx][j - 1]
            else:
                s_num = distance[prev_row_idx][j - 1] + 1
                i_num = distance[cur_row_idx][j - 1] + 1
                d_num = distance[prev_row_idx][j] + 1
                distance[cur_row_idx][j] = min(s_num, i_num, d_num)

    return distance[m % 2][n]


def word_errors(reference, hypothesis, ignore_case=False, delimiter=' '):

    if ignore_case == True:
        reference = reference.lower()
        hypothesis = hypothesis.lower()

    ref_words = reference.split(delimiter)
    hyp_words = hypothesis.split(delimiter)

    edit_distance = _levenshtein_distance(ref_words, hyp_words)
    return float(edit_distance), len(ref_words)


def char_errors(reference, hypothesis, ignore_case=False, remove_space=False):

    if ignore_case == True:
        reference = reference.lower()
        hypothesis = hypothesis.lower()

    join_char = ' '
    if remove_space == True:
        join_char = ''

    reference = join_char.join(filter(None, reference.split(' ')))
    hypothesis = join_char.join(filter(None, hypothesis.split(' ')))

    edit_distance = _levenshtein_distance(reference, hypothesis)
    return float(edit_distance), len(reference)


def wer(reference, hypothesis, ignore_case=False, delimiter=' '):

    edit_distance, ref_len = word_errors(reference, hypothesis, ignore_case,
                                         delimiter)

    if ref_len == 0:
        raise ValueError("Reference's word number should be greater than 0.")

    wer = float(edit_distance) / ref_len
    return wer


def cer(reference, hypothesis, ignore_case=False, remove_space=False):

    edit_distance, ref_len = char_errors(reference, hypothesis, ignore_case,
                                         remove_space)

    if ref_len == 0:
        raise ValueError("Length of reference should be greater than 0.")

    cer = float(edit_distance) / ref_len
    return cer

class TextTransform:
    """Maps characters to integers and vice versa"""
    def __init__(self):
        char_list = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя ,.?!@#$%^&*()-=_+ \n"
        # Create char_map with both lower and upper case letters
        self.char_map = {char: index for index, char in enumerate(char_list + char_list.upper())}
        self.index_map = {index: char for index, char in enumerate(char_list + char_list.upper())}

    def text_to_int(self, text):
        """ Use a character map and convert text to an integer sequence """
        int_sequence = []
        for c in text:
            if c != '':
                ch = self.char_map[c]
            int_sequence.append(ch)
        return int_sequence

    def int_to_text(self, labels):
        """ Use a character map and convert integer labels to an text sequence """
        string = []
        for i in labels:
            string.append(self.index_map[i])
        return ''.join(string)

train_audio_transforms = nn.Sequential(
    torchaudio.transforms.MelSpectrogram(sample_rate=16000, n_mels=128),
    torchaudio.transforms.FrequencyMasking(freq_mask_param=30),
    torchaudio.transforms.TimeMasking(time_mask_param=100)
)

valid_audio_transforms = torchaudio.transforms.MelSpectrogram()

text_transform = TextTransform()

def data_processing(data, data_type="train"):
    spectrograms = []
    labels = []
    input_lengths = []
    label_lengths = []
    for (waveform, utterance) in data:
        if data_type == 'train':
            spec = train_audio_transforms(waveform).squeeze(0).transpose(0, 1)
        elif data_type == 'valid':
            spec = valid_audio_transforms(waveform).squeeze(0).transpose(0, 1)
        else:
            raise Exception('data_type should be train or valid')
        spectrograms.append(spec)
        label = torch.Tensor(text_transform.text_to_int(utterance))
        labels.append(label)
        input_lengths.append(spec.shape[0]//4)
        label_lengths.append(len(label))

    spectrograms1 = nn.utils.rnn.pad_sequence(spectrograms, batch_first=True).unsqueeze(1).transpose(2, 3)

    labels = nn.utils.rnn.pad_sequence(labels, batch_first=True)

    return spectrograms1, labels, input_lengths, label_lengths


def GreedyDecoder(output, labels, label_lengths, blank_label=28, collapse_repeated=True):
    arg_maxes = torch.argmax(output, dim=2)
    decodes = []
    targets = []
    for i, args in enumerate(arg_maxes):
        decode = []
        targets.append(text_transform.int_to_text(labels[i][:label_lengths[i]].tolist()))
        for j, index in enumerate(args):
            if index != blank_label:
                if collapse_repeated and j != 0 and index == args[j -1]:
                    continue
                decode.append(index.item())
        decodes.append(text_transform.int_to_text(decode))
    return decodes, targets

"""**Layer Normalization and Residual Connections in Speech Recognition Network**"""

class CNNLayerNorm(nn.Module):
    """Layer normalization built for cnns input"""
    def __init__(self, n_feats):
        super(CNNLayerNorm, self).__init__()
        self.layer_norm = nn.LayerNorm(n_feats)

    def forward(self, x):
        # x (batch, channel, feature, time)
        x = x.transpose(2, 3).contiguous() # (batch, channel, time, feature)
        x = self.layer_norm(x)
        return x.transpose(2, 3).contiguous() # (batch, channel, feature, time)


class ResidualCNN(nn.Module):
    """Residual CNN inspired by https://arxiv.org/pdf/1603.05027.pdf
        except with layer norm instead of batch norm
    """
    def __init__(self, in_channels, out_channels, kernel, stride, dropout, n_feats):
        super(ResidualCNN, self).__init__()

        self.cnn1 = nn.Conv2d(in_channels, out_channels, kernel, stride, padding=kernel//2)
        self.cnn2 = nn.Conv2d(out_channels, out_channels, kernel, stride, padding=kernel//2)
        self.dropout1 = nn.Dropout(dropout)
        self.dropout2 = nn.Dropout(dropout)
        self.layer_norm1 = CNNLayerNorm(n_feats)
        self.layer_norm2 = CNNLayerNorm(n_feats)

    def forward(self, x):
        residual = x  # (batch, channel, feature, time)
        x = self.layer_norm1(x)
        x = F.gelu(x)
        x = self.dropout1(x)
        x = self.cnn1(x)
        x = self.layer_norm2(x)
        x = F.gelu(x)
        x = self.dropout2(x)
        x = self.cnn2(x)
        x += residual
        return x # (batch, channel, feature, time)


class BidirectionalGRU(nn.Module):

    def __init__(self, rnn_dim, hidden_size, dropout, batch_first):
        super(BidirectionalGRU, self).__init__()

        self.BiGRU = nn.GRU(
            input_size=rnn_dim, hidden_size=hidden_size,
            num_layers=1, batch_first=batch_first, bidirectional=True)
        self.layer_norm = nn.LayerNorm(rnn_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        x = self.layer_norm(x)
        x = F.gelu(x)
        x, _ = self.BiGRU(x)
        x = self.dropout(x)
        return x


class SpeechRecognitionModel(nn.Module):

    def __init__(self, n_cnn_layers, n_rnn_layers, rnn_dim, n_class, n_feats, stride=2, dropout=0.1):
        super(SpeechRecognitionModel, self).__init__()
        n_feats = n_feats//2
        self.cnn = nn.Conv2d(1, 32, 3, stride=stride, padding=3//2)  # cnn for extracting heirachal features

        # n residual cnn layers with filter size of 32
        self.rescnn_layers = nn.Sequential(*[
            ResidualCNN(32, 32, kernel=3, stride=1, dropout=dropout, n_feats=n_feats)
            for _ in range(n_cnn_layers)
        ])
        self.fully_connected = nn.Linear(n_feats*32, rnn_dim)
        self.birnn_layers = nn.Sequential(*[
            BidirectionalGRU(rnn_dim=rnn_dim if i==0 else rnn_dim*2,
                             hidden_size=rnn_dim, dropout=dropout, batch_first=i==0)
            for i in range(n_rnn_layers)
        ])
        self.classifier = nn.Sequential(
            nn.Linear(rnn_dim*2, rnn_dim),  # birnn returns rnn_dim*2
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(rnn_dim, n_class)
        )

    def forward(self, x):
        x = self.cnn(x)
        x = self.rescnn_layers(x)
        sizes = x.size()
        x = x.view(sizes[0], sizes[1] * sizes[2], sizes[3])  # (batch, feature, time)
        x = x.transpose(1, 2) # (batch, time, feature)
        x = self.fully_connected(x)
        x = self.birnn_layers(x)
        x = self.classifier(x)
        return x

import pandas as pd
import librosa
import os
import numpy as np
import torch

"""**Load the data**"""

file = pd.read_excel(r'/content/drive/MyDrive/Dataset_PC/RSDA Dataset v5/Data.xlsx')
file['text'] = file['text'].str.replace(r'[^а-яА-ЯёЁ\s]', '', regex=True)
y = list(file['text'])  # Assuming 'text' corresponds to your audio files
ids = list(file['id'])   # Assuming the first column is 'id'

"""Directory containing audio files"""

dir_name = r"/content/drive/MyDrive/Dataset_PC/RSDA Dataset v5/Speaker_3"
files_in_dir = os.listdir(dir_name)

X = []
filtered_y = []  # To hold corresponding y values
filtered_ids = []  # To hold corresponding ids

# Initialize a counter to keep track of the number of files processed
file_count = 0
max_files = 10  # Set the maximum number of files to process

for e in range(len(ids)):  # Iterate through all ids
    if file_count >= max_files:  # Check if the maximum number of files has been reached
        break  # Exit the loop if the limit is reached

    file_name = f'{ids[e]}.wav'
    file_path = os.path.join(dir_name, file_name)  # Create the full file path
    try:
        # Load the audio file
        sampl = librosa.load(file_path, sr=16000)[0]
        sampl = sampl[np.newaxis, :]  # Add a new axis
        X.append(torch.Tensor(sampl))
        filtered_y.append(y[e])  # Append the corresponding text
        filtered_ids.append(ids[e])  # Append the corresponding id

        file_count += 1  # Increment the counter after successfully processing a file

    except FileNotFoundError:
        print(f"File {file_name} not found, skipping text: '{y[e]}'")
        # Skip the text as the audio file is missing

"""Check the shapes of X and filtered_y"""

print(f"Length of X: {len(X)}")
print(f"Length of filtered_y: {len(filtered_y)}")

"""Check the shape of the first sample if it exists"""

if X:
    print("First audio shape:", X[0].shape)
else:
    print("No audio files were loaded.")

"""Create a new DataFrame with the filtered results"""

filtered_df = pd.DataFrame({
    'id': filtered_ids,
    'text': filtered_y
})

"""**load Excel file**"""

filtered_df.to_excel(r'D:\RSDA Dataset v5\Adjusted_Data.xlsx', index=False)
print("Adjusted DataFrame loaded succefully'.")

"""Output the shape of the first audio sample"""

if X:
    print("Shape of first audio sample:", X[0].shape)
else:
    print("No audio samples to display.")

X[0].shape

char_map = {"а": 0, "б": 1, "в": 2, "г": 3, "д": 4, "е": 5, "ё": 6, "ж": 7, "з": 8, "и": 9, "й": 10,
            "к": 11, "л": 12, "м": 13, "н": 14, "о": 15, "п": 16, "р": 17, "с": 18, "т": 19, "у": 20,
            "ф": 21, "ч": 22, "ц": 23, "ш": 24, "щ": 25, "ъ": 26, "ы": 27, "ь": 28, "э": 29, "ю": 30,
            "я": 31, "х": 32, " ": 33,"\n": 34}

def remove_characters(sentence):
    # sentence = sentence.lower()
    sentence = ''.join(filter(lambda x: x in char_map, sentence))
    return sentence.replace('\n', '')

y = list(map(remove_characters, y))

from sklearn.model_selection import train_test_split

X_train, X_test, y_train, y_test = train_test_split(X, filtered_y, test_size=0.1)

"""**Custom Dataset for Audio-Text Pairing in Speech Recognition**"""

from torch.utils.data import Dataset

class AudioDataset(Dataset):
    def __init__(self, audio_list, text_list):
        self.audio_list = audio_list
        self.text_list = text_list

    def __len__(self):
        return len(self.text_list)

    def __getitem__(self, index):
        audio = self.audio_list[index]
        text = self.text_list[index]
        return audio, text

"""**Deep Learning Model for Speech Recognition Using CNN and Bidirectional GRU**"""

class SpeechRecognitionModel1(nn.Module):
    def __init__(self, num_classes):
        super(SpeechRecognitionModel1, self).__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(1, 64, kernel_size=(3,3), stride=(1,1), padding=(1,1)),
            nn.BatchNorm2d(64),
            nn.GELU(),
            nn.MaxPool2d(kernel_size=(2,2), stride=(2,2)),
            nn.Conv2d(64, 128, kernel_size=(3,3), stride=(1,1), padding=(1,1)),
            nn.BatchNorm2d(128),
            nn.GELU(),
            nn.Conv2d(128, 64, kernel_size=(3,3), stride=(1,1), padding=(1,1)),
            nn.BatchNorm2d(64),
            nn.GELU(),
            nn.Conv2d(64, 64, kernel_size=(3,3), stride=(1,1), padding=(1,1)),
            nn.GELU(),
            nn.MaxPool2d(kernel_size=(2,2), stride=(2,2)),
        )
        self.rnn = nn.GRU(input_size=2048,
                    hidden_size=256,
                    num_layers=1,
                    batch_first=True,
                    bidirectional=True)
        self.fc = nn.Sequential(
            nn.Linear(512, 128),
            nn.GELU(),
            nn.Linear(128, num_classes),
        )
        self.softmax = nn.LogSoftmax(dim=1)
    def forward(self, x):
        x = self.conv(x)
        x = x.permute(0, 3, 1, 2)
        x = x.view(x.size(0), x.size(1), -1)
        x, _ = self.rnn(x)
        x = self.fc(x)
        x = self.softmax(x)
        return x

"""nn.Linear(512, 128),<br>
            nn.GELU(),<br>
          nn.Dropout(0.35),

**Training and Evaluating a CNN-GRU Model for Speech Recognition**
"""

import torch
import torch.optim as optim
import torch.nn as nn
import torch.nn.functional as F
from torch.utils import data

# Assuming you have defined your AudioDataset, SpeechRecognitionModel1, GreedyDecoder, cer, and wer functions

class IterMeter(object):
    """Keeps track of total iterations."""
    def __init__(self):
        self.val = 0

    def step(self):
        self.val += 1

    def get(self):
        return self.val

def train(model, device, train_loader, criterion, optimizer, scheduler, epoch, iter_meter):
    model.train()
    data_len = len(train_loader.dataset)
    scaler = torch.cuda.amp.GradScaler()  # Initialize GradScaler for mixed precision

    for batch_idx, _data in enumerate(train_loader):
        spectrograms, labels, input_lengths, label_lengths = _data
        spectrograms, labels = spectrograms.to(device), labels.to(device)

        optimizer.zero_grad()

        with torch.cuda.amp.autocast():  # Enable mixed precision
            output = model(spectrograms)  # (batch, time, n_class)
            output = F.log_softmax(output, dim=2)
            output = output.transpose(0, 1)  # (time, batch, n_class)

            # Calculate the loss
            loss = criterion(output, labels, input_lengths, label_lengths)
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)

        scaler.scale(loss).backward()  # Scale the loss and call backward
        scaler.step(optimizer)          # Update the optimizer
        scaler.update()                 # Update the scale for next iteration
        scheduler.step()
        iter_meter.step()

        # Clear cache to free up memory
        torch.cuda.empty_cache()

        # Debugging: Check for NaN values
        assert not torch.isnan(spectrograms).any(), "Input contains NaN values"
        assert not torch.isnan(labels).any(), "Labels contain NaN values"

        if batch_idx % 10 == 0 or batch_idx == data_len:
            print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                epoch, batch_idx * len(spectrograms), data_len,
                100. * batch_idx / len(train_loader), loss.item()))

def test(model, device, test_loader, criterion, epoch, iter_meter):
    print('\nevaluating...')
    model.eval()
    test_loss = 0
    test_cer, test_wer = [], []

    with torch.no_grad():
        for i, _data in enumerate(test_loader):
            spectrograms, labels, input_lengths, label_lengths = _data
            spectrograms, labels = spectrograms.to(device), labels.to(device)

            output = model(spectrograms)  # (batch, time, n_class)
            output = F.log_softmax(output, dim=2)
            output = output.transpose(0, 1)  # (time, batch, n_class)

            loss = criterion(output, labels, input_lengths, label_lengths)
            test_loss += loss.item() / len(test_loader)

            # Assuming you have defined GreedyDecoder, cer, and wer functions
            decoded_preds, decoded_targets = GreedyDecoder(output.transpose(0, 1), labels, label_lengths)
            for j in range(len(decoded_preds)):
                test_cer.append(cer(decoded_targets[j], decoded_preds[j]))
                test_wer.append(wer(decoded_targets[j], decoded_preds[j]))

    avg_cer = sum(test_cer) / len(test_cer) if test_cer else 0
    avg_wer = sum(test_wer) / len(test_wer) if test_wer else 0

    print('Test set: Average loss: {:.4f}, Average CER: {:.4f}, Average WER: {:.4f}\n'.format(
        test_loss, avg_cer, avg_wer))

def save_model(model, optimizer, epoch, filename='model_checkpoint.pth'):
    """Save the model checkpoint including optimizer state."""
    torch.save({
        'epoch': epoch,
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': optimizer.state_dict(),
    }, filename)
    print(f'Model saved to {filename} at epoch {epoch}')

def load_model(model, optimizer, filename='model_checkpoint.pth'):
    """Load the model checkpoint including optimizer state."""
    checkpoint = torch.load(filename)
    model.load_state_dict(checkpoint['model_state_dict'])
    optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    print(f'Model loaded from {filename} (epoch {checkpoint["epoch"]})')

def main(learning_rate=5e-4, batch_size=10, epochs=10):
    hparams = {
        "n_cnn_layers": 2,
        "n_rnn_layers": 2,
        "rnn_dim": 256,
        "n_class": 34,
        "n_feats": 128,
        "stride": 2,
        "dropout": 0.1,
        "learning_rate": learning_rate,
        "batch_size": batch_size,
        "epochs": epochs
    }

    use_cuda = torch.cuda.is_available()
    torch.manual_seed(7)
    device = torch.device("cuda" if use_cuda else "cpu")

    # Assuming X_train, y_train, X_test, y_test are defined
    train_dataset = AudioDataset(X_train, y_train)
    test_dataset = AudioDataset(X_test, y_test)

    kwargs = {'num_workers': 1, 'pin_memory': True} if use_cuda else {}
    train_loader = data.DataLoader(dataset=train_dataset,
                                   batch_size=hparams['batch_size'],
                                   shuffle=True,
                                   collate_fn=lambda x: data_processing(x, 'train'),
                                   **kwargs)
    test_loader = data.DataLoader(dataset=test_dataset,
                                  batch_size=hparams['batch_size'],
                                  shuffle=False,
                                  collate_fn=lambda x: data_processing(x, 'valid'),
                                  **kwargs)

    model = SpeechRecognitionModel1(hparams['n_class']).to(device)

    print(model)
    print('Num Model Parameters', sum([param.nelement() for param in model.parameters()]))

    optimizer = optim.AdamW(model.parameters(), hparams['learning_rate'])
    criterion = nn.CTCLoss(blank=28).to(device)
    scheduler = optim.lr_scheduler.OneCycleLR(optimizer, max_lr=hparams['learning_rate'],
                                               steps_per_epoch=int(len(train_loader)),
                                               epochs=hparams['epochs'],
                                               anneal_strategy='linear')

    iter_meter = IterMeter()
    for epoch in range(1, epochs + 1):
        train(model, device, train_loader, criterion, optimizer, scheduler, epoch, iter_meter)
        test(model, device, test_loader, criterion, epoch, iter_meter)

        # Save the model after each epoch
        save_model(model, optimizer, epoch)

if __name__ == '__main__':
    learning_rate = 0.0001
    batch_size = 10  # Adjust as necessary
    epochs = 100
    main(learning_rate, batch_size, epochs)